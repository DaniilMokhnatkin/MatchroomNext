"""Safe backup and merge helpers used by the Matchroom installer.

The installer calls this module through the embedded Python runtime.  SQLite's
backup API is deliberately used instead of copying history.sqlite3 so an old
database is still complete when it has an active WAL file.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time


DATABASE = "history.sqlite3"
TABLES = (
    "matches", "settings", "ranking_snapshots", "match_ranking_snapshots",
    "match_rating_changes", "leaderboard_gaps",
)


def _database(value: Path | str) -> Path:
    path = Path(value).resolve()
    return path / DATABASE if path.is_dir() or path.suffix.lower() != ".sqlite3" else path


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".new")
    temporary.unlink(missing_ok=True)
    origin = sqlite3.connect(source)
    copy = sqlite3.connect(temporary)
    try:
        origin.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Не удалось проверить резервную копию {source}")
        copy.commit()
    finally:
        copy.close()
        origin.close()
    temporary.replace(destination)


def backup_data(source: Path | str, destination: Path | str) -> dict:
    source_dir = Path(source).resolve()
    destination_dir = Path(destination).resolve()
    source_db = _database(source_dir)
    if not source_db.is_file():
        return {"backed_up": False, "reason": "database_missing"}
    destination_dir.mkdir(parents=True, exist_ok=True)
    _backup_database(source_db, destination_dir / DATABASE)
    for name in ("install-state.json",):
        candidate = source_dir / name
        if candidate.is_file():
            shutil.copy2(candidate, destination_dir / name)
    return {"backed_up": True, "database": str(destination_dir / DATABASE)}


def _rows(connection: sqlite3.Connection, table: str):
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return columns, []
    return columns, connection.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()


def _insert(connection: sqlite3.Connection, table: str, columns: list[str], row, mode="IGNORE"):
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT OR {mode} INTO {table} ({','.join(columns)}) VALUES ({placeholders})", row)


def _merge_rating_change(target: sqlite3.Connection, columns: list[str], row) -> bool:
    data = dict(zip(columns, row))
    match_id = data.get("match_id")
    existing = target.execute(
        "SELECT status,delta,global_verified,calculation_version FROM match_rating_changes WHERE match_id=?", (match_id,)
    ).fetchone()
    if existing is None:
        _insert(target, "match_rating_changes", columns, row)
        return True
    old_status, old_delta, old_verified, old_version = existing
    new_status, new_delta = data.get("status"), data.get("delta")
    old_verified = bool(old_verified) or old_version >= 2
    new_verified = bool(data.get('global_verified')) or data.get('calculation_version', 0) >= 2
    if old_status == 'confirmed' and new_status == 'confirmed' and bool(old_verified) != new_verified:
        if new_verified:
            _insert(target, 'match_rating_changes', columns, row, 'REPLACE')
            return True
        return False
    if old_status == "confirmed" and new_status == "confirmed" and old_delta != new_delta:
        target.execute(
            "UPDATE match_rating_changes SET status='unavailable',delta=NULL," 
            "message=? WHERE match_id=?",
            ("Найдены противоречащие подтверждённые снимки в старых архивах.", match_id),
        )
        return True
    priority = {"unavailable": 0, "pending": 1, "confirmed": 2}
    if priority.get(new_status, 0) > priority.get(old_status, 0):
        _insert(target, "match_rating_changes", columns, row, "REPLACE")
        return True
    return False


def merge_data(target: Path | str, sources: list[Path | str]) -> dict:
    """Merge old Matchroom data directories into target, idempotently."""
    target_dir = Path(target).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_db = target_dir / DATABASE
    target_was_empty = not target_db.exists()

    # History owns all schema migrations. Upgrade the destination before merge.
    from match_history import History
    destination_history = History(target_dir)
    destination_history.close()

    candidates = []
    target_resolved = target_db.resolve()
    for source in sources:
        source_dir = Path(source).resolve()
        source_db = _database(source_dir)
        if source_db.is_file() and source_db.resolve() != target_resolved:
            candidates.append((source_db.stat().st_mtime_ns, source_dir, source_db))
    candidates.sort()

    stats = {"sources": len(candidates), "matches": 0, "snapshots": 0,
             "rating_changes": 0, "settings_source": None}
    if not candidates:
        return stats

    with tempfile.TemporaryDirectory(prefix="matchroom-merge-") as temporary_root:
        upgraded = []
        for index, (modified, source_dir, source_db) in enumerate(candidates):
            temporary = Path(temporary_root) / str(index)
            temporary.mkdir()
            _backup_database(source_db, temporary / DATABASE)
            old = History(temporary)
            old.close()
            upgraded.append((modified, source_dir, temporary / DATABASE))

        with closing(sqlite3.connect(target_db)) as destination:
            destination.execute("PRAGMA foreign_keys=OFF")
            for _modified, _source_dir, source_db in upgraded:
                with closing(sqlite3.connect(source_db)) as origin:
                    for table in ("matches", "ranking_snapshots", "match_ranking_snapshots"):
                        columns, rows = _rows(origin, table)
                        for row in rows:
                            before = destination.total_changes
                            _insert(destination, table, columns, row)
                            added = destination.total_changes > before
                            if added and table == "matches":
                                stats["matches"] += 1
                            elif added:
                                stats["snapshots"] += 1
                    columns, rows = _rows(origin, "match_rating_changes")
                    for row in rows:
                        if _merge_rating_change(destination, columns, row):
                            stats["rating_changes"] += 1
                    columns, rows = _rows(origin, "leaderboard_gaps")
                    for row in rows:
                        _insert(destination, "leaderboard_gaps", columns, row, "REPLACE")

            if target_was_empty:
                # The most recently used archive supplies the initial preferences.
                _modified, settings_source, latest_db = upgraded[-1]
                with closing(sqlite3.connect(latest_db)) as origin:
                    columns, rows = _rows(origin, "settings")
                    destination.execute("DELETE FROM settings")
                    for row in rows:
                        _insert(destination, "settings", columns, row, "REPLACE")
                stats["settings_source"] = str(settings_source)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Объединённая база Matchroom не прошла проверку целостности.")
            destination.commit()
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--source", required=True, type=Path)
    backup.add_argument("--destination", required=True, type=Path)
    merge = sub.add_parser("merge")
    merge.add_argument("--target", required=True, type=Path)
    merge.add_argument("--source", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    result = (backup_data(args.source, args.destination) if args.command == "backup"
              else merge_data(args.target, args.source))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
