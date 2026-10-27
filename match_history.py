"""Persistent match metadata and a polling watcher; never modifies replay files."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from ranking_seasons import current_season, season_end_date

RANKED_REPLAY_PATTERN = re.compile(r'^(?P<season>[1-9][0-9]{0,2})_', re.IGNORECASE)
RATING_CONFIRMATION_SECONDS = 10 * 60
RATING_CONFIRMATION_INTERVAL_SECONDS = 5


def ranked_season(filename: str) -> int | None:
    """Return the season prefix from a Forts ranked replay filename."""
    match = RANKED_REPLAY_PATTERN.match(Path(filename).name)
    return int(match['season']) if match else None


def is_ranked_replay(filename: str) -> bool:
    return ranked_season(filename) is not None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _commanders_from_log(path: Path) -> dict[str, dict[str, str]]:
    """Read commander choices recorded alongside the exact saved replay name."""
    matches = {}
    chosen = {}
    contents = path.read_bytes()
    # Forts writes log.txt as UTF-16 LE with a BOM on Windows. Older copied
    # logs may be UTF-8, so keep that format as a fallback.
    encoding = 'utf-16' if contents.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    for line in contents.decode(encoding, errors='replace').splitlines():
        side = re.search(r'Team([12]) commander:\s*(commander-[a-z0-9-]+)?\s*$', line, re.I)
        if side:
            chosen[side[1]] = side[2] or ''
            continue
        saved = re.search(r'Replay saved as replays/([^/\\]+\.fwr)\s*$', line, re.I)
        if saved:
            if len(chosen) == 2 and any(chosen.values()):
                matches[saved[1].casefold()] = {side: slug for side, slug in chosen.items() if slug}
            chosen = {}
    return matches


def _match_identity(record: dict, content_key: str) -> str:
    """Build a stable game id without treating identical headers as one game.

    Forts can write the same header for several consecutive games against the
    same player.  The ranked filename timestamp is the per-game identifier.
    """
    date = record.get('date') or ''
    if not date:
        return content_key
    players = sorted(
        (str(player.get('steam_id') or ''), int(player.get('side') or 0))
        for player in record.get('players', [])
    )
    value = json.dumps({
        'season': record.get('season'), 'date': date,
        'map': record.get('map') or '', 'players': players,
    }, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(b'forts-match-v2\0' + value).hexdigest()


def _match_epoch(value: str) -> float | None:
    """Interpret Forts' filename timestamp in the computer's local timezone."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y%m%d_%H%M%S').astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _replay_started_at(path: Path, filename: str) -> float | None:
    """Use file creation time when it proves the replay was written live."""
    match = re.search(r'_(20\d{6}_\d{6})', filename)
    named_time = _match_epoch(match[1]) if match else None
    if named_time is None:
        return None
    try:
        stat = path.stat()
        duration = stat.st_mtime - stat.st_ctime
        # A replay that grew for several seconds records a match in progress.
        # A copied or one-shot imported replay does not qualify.
        if 10 <= duration <= 6 * 60 * 60 and abs(stat.st_ctime - named_time) <= 6 * 60 * 60:
            return stat.st_ctime
    except OSError:
        pass
    return None


def discover_replays():
    try:
        from steam_native import locate_dll
        users = locate_dll().parent / 'users'
        return [str(p) for p in users.glob('*/replays') if p.is_dir()]
    except (OSError, RuntimeError):
        return []


class History:
    def __init__(self, directory: Path, folders=None):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / 'history.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, date TEXT NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS ranking_snapshots (
            season INTEGER NOT NULL, steam_id TEXT NOT NULL, observed_at REAL NOT NULL,
            rank INTEGER, score INTEGER, total INTEGER,
            PRIMARY KEY (season, steam_id, observed_at))''')
        self.db.execute('CREATE INDEX IF NOT EXISTS ranking_lookup ON ranking_snapshots(season, steam_id, observed_at DESC)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS match_ranking_snapshots (
            match_id TEXT NOT NULL, season INTEGER NOT NULL, steam_id TEXT NOT NULL,
            observed_at REAL NOT NULL, rank INTEGER, score INTEGER, total INTEGER,
            PRIMARY KEY (match_id, steam_id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS match_rating_changes (
            match_id TEXT PRIMARY KEY, season INTEGER NOT NULL, steam_id TEXT,
            status TEXT NOT NULL, before_rank INTEGER, before_score INTEGER,
            after_rank INTEGER, after_score INTEGER, delta INTEGER,
            started_at REAL NOT NULL, deadline REAL NOT NULL, observed_at REAL,
            message TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS leaderboard_gaps (
            season INTEGER NOT NULL, steam_id TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY (season,steam_id))''')
        for table in ('match_ranking_snapshots', 'match_rating_changes'):
            columns = {row[1] for row in self.db.execute(f'PRAGMA table_info({table})')}
            if 'global_verified' not in columns:
                self.db.execute(f'ALTER TABLE {table} ADD COLUMN global_verified INTEGER NOT NULL DEFAULT 0')
        change_columns = {row[1] for row in self.db.execute('PRAGMA table_info(match_rating_changes)')}
        if 'calculation_version' not in change_columns:
            self.db.execute('ALTER TABLE match_rating_changes ADD COLUMN calculation_version INTEGER NOT NULL DEFAULT 0')
        # Older Matchroom versions imported every .fwr. Keep only files that
        # use Forts' numeric ranked-season filename prefix.
        rows = self.db.execute('SELECT id, data FROM matches').fetchall()
        rebuilt = {}
        for old_id, raw in rows:
            record = json.loads(raw)
            if not is_ranked_replay(record.get('file', '')):
                continue
            season = ranked_season(record.get('file', ''))
            if season is not None and record.get('season') != season:
                record['season'] = season
            record.setdefault('match_epoch', _match_epoch(record.get('date', '')))
            if 'outcome' not in record:
                winner, outcome = None, 'unknown'
                source = record.get('source')
                try:
                    if source and Path(source).is_file():
                        from forts_replay import read_replay_winner
                        winner, outcome = read_replay_winner(Path(source))
                except (OSError, ValueError):
                    pass
                record.update(winner_side=winner, outcome=outcome)
            content_key = record.get('content_key') or old_id
            record['content_key'] = content_key
            record.setdefault('imported_at', 0)
            source = record.get('source')
            if source:
                try:
                    source_path = Path(source)
                    if source_path.is_file():
                        record['played_at_ns'] = source_path.stat().st_mtime_ns
                        started_at = _replay_started_at(source_path, record.get('file', ''))
                        if started_at is not None:
                            record['replay_started_at'] = started_at
                        if not record.get('file_hash'):
                            record['file_hash'] = _file_hash(source_path)
                except OSError:
                    pass
            match_id = _match_identity(record, content_key)
            record['match_id'] = match_id
            previous = rebuilt.get(match_id)
            if previous is None or (previous.get('outcome') == 'unknown' and record.get('outcome') != 'unknown'):
                rebuilt[match_id] = record
        by_hash = {}
        for match_id, record in list(rebuilt.items()):
            file_hash = record.get('file_hash')
            if file_hash and file_hash in by_hash:
                kept_id = by_hash[file_hash]
                kept = rebuilt[kept_id]
                if kept.get('outcome') == 'unknown' and record.get('outcome') != 'unknown':
                    kept['winner_side'] = record.get('winner_side')
                    kept['outcome'] = record.get('outcome')
                if not kept.get('commanders') and record.get('commanders'):
                    kept['commanders'] = record['commanders']
                if not kept.get('replay_started_at') and record.get('replay_started_at'):
                    kept['replay_started_at'] = record['replay_started_at']
                new_time, old_time = record.get('played_at_ns') or 0, kept.get('played_at_ns') or 0
                if new_time and (not old_time or new_time < old_time):
                    kept.update(file=record.get('file'), date=record.get('date'),
                                source=record.get('source'), played_at_ns=new_time)
                del rebuilt[match_id]
            elif file_hash:
                by_hash[file_hash] = match_id
        # Re-key old databases and collapse rows that describe the same ranked
        # game. This also repairs duplicates created by earlier builds.
        self.db.execute('DELETE FROM matches')
        self.db.executemany(
            'INSERT INTO matches VALUES (?,?,?)',
            ((match_id, record.get('date') or '', json.dumps(record, ensure_ascii=True))
             for match_id, record in rebuilt.items()))
        self.db.commit()
        saved = self.db.execute("SELECT value FROM settings WHERE key='watch'").fetchone()
        self.config = json.loads(saved[0]) if saved else {'enabled': True, 'folders': folders if folders is not None else discover_replays()}
        saved_language = self.db.execute("SELECT value FROM settings WHERE key='language'").fetchone()
        language_file = directory / 'ui-language.txt'
        if saved_language and saved_language[0] in ('ru', 'en'):
            self.language = saved_language[0]
        else:
            try:
                preferred = language_file.read_text(encoding='utf-8').strip().lower()
            except OSError:
                preferred = 'ru'
            self.language = preferred if preferred in ('ru', 'en') else 'ru'
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('language',?)", (self.language,))
            self.db.commit()
        owner = self.db.execute("SELECT value FROM settings WHERE key='owner_steam_id'").fetchone()
        latest_owner = self._latest_match_owner_id()
        self.owner_steam_id = (latest_owner or
                               (owner[0] if owner and self._valid_owner_id(owner[0]) else
                                self._owner_id_from_folders(self.config.get('folders', []))))
        saved_mode = self.db.execute("SELECT value FROM settings WHERE key='owner_mode'").fetchone()
        self.owner_mode = 'manual' if saved_mode and saved_mode[0] == 'manual' else 'auto'
        if self.owner_mode == 'manual' and owner and self._valid_owner_id(owner[0]):
            self.owner_steam_id = owner[0]
        if self.owner_mode == 'auto' and latest_owner and (not owner or owner[0] != latest_owner):
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('owner_steam_id',?)", (latest_owner,))
            self.db.commit()
        self.revision = time.time_ns()
        self.status = {'scanning': False, 'checked': 0, 'errors': [],
                       'folder_errors': [], 'replay_errors': [], 'last_scan': None}
        self.stop_event = threading.Event()
        self.thread = None
        self.seen = {}
        self.pending = {}
        self.header_seen = set()
        self.known_profiles = {}
        self.log_seen = {}
        self.log_commanders = {}
        self.rating_status = {'last_checked': None, 'status': 'waiting', 'message': None}
        self.rating_thread = None
        self.rating_wake = threading.Event()
        self.live_rating_pending = {}
        for record in rebuilt.values():
            if record.get('live_discovered_at') and record.get('outcome') == 'unknown':
                ids = {p['steam_id'] for p in record.get('players', []) if p.get('steam_id')}
                captured = {row[0] for row in self.db.execute(
                    'SELECT steam_id FROM match_ranking_snapshots WHERE match_id=? AND score IS NOT NULL',
                    (record['match_id'],)).fetchall()}
                if not ids <= captured:
                    self.live_rating_pending[record['match_id']] = (int(record['season']), sorted(ids))

    def add(self, data: bytes, filename: str, source=None, winner=None, outcome=None,
            file_hash=None, played_at_ns=None, live=False, started_at=None):
        from forts_replay import header_length, parse_replay, winner_from_bytes
        season = ranked_season(filename)
        if season is None:
            raise ValueError('В Matchroom добавляются только рейтинговые реплеи: имя файла должно начинаться с номера сезона, например «43_».')
        header = data[:header_length(data) + 4]
        content_key = hashlib.sha256(header).hexdigest()
        parsed = parse_replay(header, filename)
        if outcome is None:
            winner, outcome = winner_from_bytes(data) if len(data) > len(header) else (None, 'unknown')
        if file_hash is None and len(data) > len(header):
            file_hash = hashlib.sha256(data).hexdigest()
        stamp = re.search(r'_(20\d{6}_\d{6})', filename)
        date = stamp[1] if stamp else ''
        parsed.update(date=date, source=source, season=season, content_key=content_key,
                      winner_side=winner, outcome=outcome, imported_at=time.time_ns(),
                      file_hash=file_hash, played_at_ns=played_at_ns or 0,
                      match_epoch=_match_epoch(date))
        if started_at is not None:
            parsed['replay_started_at'] = started_at
        if live:
            parsed['live_discovered_at'] = time.time()
        match_id = _match_identity(parsed, content_key)
        parsed['match_id'] = match_id
        with self.lock:
            existing = self.db.execute('SELECT data FROM matches WHERE id=?', (match_id,)).fetchone()
            if not existing and file_hash:
                for raw, in self.db.execute('SELECT data FROM matches'):
                    candidate = json.loads(raw)
                    if candidate.get('file_hash') == file_hash:
                        existing = (raw,)
                        match_id = candidate['match_id']
                        parsed['match_id'] = match_id
                        break
            # A manually renamed copy may no longer carry its original timestamp.
            # In that case the header fingerprint is still safe for de-duplication.
            if not existing and not date:
                for raw, in self.db.execute('SELECT data FROM matches'):
                    candidate = json.loads(raw)
                    if candidate.get('content_key') == content_key:
                        existing = (raw,)
                        match_id = candidate['match_id']
                        parsed['match_id'] = match_id
                        break
            if existing:
                saved = json.loads(existing[0])
                changed = False
                visible_changed = False
                if live and not saved.get('live_discovered_at'):
                    saved['live_discovered_at'] = parsed['live_discovered_at']
                    changed = True
                if started_at is not None and saved.get('replay_started_at') != started_at:
                    saved['replay_started_at'] = started_at
                    changed = visible_changed = True
                finished = saved.get('outcome') == 'unknown' and outcome != 'unknown'
                if saved.get('outcome') == 'unknown' and outcome != 'unknown':
                    saved.update(winner_side=winner, outcome=outcome)
                    changed = visible_changed = True
                if source and (not saved.get('source') or saved.get('source') == source):
                    if played_at_ns and (not saved.get('played_at_ns') or finished):
                        saved['played_at_ns'] = played_at_ns
                        changed = visible_changed = True
                    if file_hash and saved.get('file_hash') != file_hash:
                        saved['file_hash'] = file_hash
                        changed = True
                if source and not saved.get('source'):
                    saved['source'] = source
                    changed = True
                if changed:
                    self.db.execute('UPDATE matches SET data=? WHERE id=?',
                                    (json.dumps(saved, ensure_ascii=True), match_id))
                    self.db.commit()
                    if visible_changed:
                        self.revision += 1
                if finished and saved.get('live_discovered_at'):
                    self._queue_finished_match_rating(match_id, saved)
                if live and not self.db.execute(
                        'SELECT 1 FROM match_ranking_snapshots WHERE match_id=? LIMIT 1',
                        (match_id,)).fetchone():
                    self.live_rating_pending[match_id] = (season, sorted({
                        p['steam_id'] for p in saved.get('players', []) if p.get('steam_id')}))
                    self.rating_wake.set()
                self._sync_owner_to_latest_match()
                return saved, False
            self.db.execute('INSERT INTO matches VALUES (?,?,?)', (match_id, date, json.dumps(parsed, ensure_ascii=True)))
            self.db.commit()
            self.revision += 1
            self._sync_owner_to_latest_match()
            if live:
                self.live_rating_pending[match_id] = (season, sorted({
                    p['steam_id'] for p in parsed.get('players', []) if p.get('steam_id')}))
                self.rating_wake.set()
        return parsed, True

    def _set_match_rating_change(self, match_id, season, steam_id, status, *,
                                 before_rank=None, before_score=None, after_rank=None,
                                 after_score=None, delta=None, started_at=None,
                                 deadline=None, observed_at=None, message=None, global_verified=False,
                                 calculation_version=0):
        now = time.time()
        self.db.execute(
            'INSERT OR REPLACE INTO match_rating_changes '
            '(match_id,season,steam_id,status,before_rank,before_score,after_rank,after_score,delta,started_at,deadline,observed_at,message,global_verified,calculation_version) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (match_id, int(season), steam_id, status, before_rank, before_score,
             after_rank, after_score, delta, started_at if started_at is not None else now,
             deadline if deadline is not None else now, observed_at, message, int(global_verified), calculation_version))
        self.db.commit()
        self.revision += 1

    def _queue_finished_match_rating(self, match_id, record):
        """Queue a bounded post-game check only when the live start was captured."""
        previous = self.db.execute(
            'SELECT status FROM match_rating_changes WHERE match_id=?', (match_id,)).fetchone()
        if previous and previous[0] in ('pending', 'confirmed'):
            return
        season = int(record.get('season') or 0)
        owner_id = self._match_owner_id(record)
        participants = sorted({p.get('steam_id') for p in record.get('players', [])
                              if self._valid_owner_id(p.get('steam_id'))})
        live_started = float(record.get('live_discovered_at') or 0)
        reason = None
        if record.get('outcome') == 'unknown' or not record.get('outcome'):
            reason = 'Результат матча не подтверждён.'
        elif len(participants) != 2 or owner_id not in participants:
            reason = 'Для снимка нужны два участника 1v1, включая владельца реплея.'
        elif any(p.get('side') for p in record.get('players', [])
                 if p.get('steam_id') in participants) and len({
                     p.get('side') for p in record.get('players', [])
                     if p.get('steam_id') in participants and p.get('side')}) < 2:
            reason = 'Участники матча должны быть на разных сторонах.'
        start_rows = {row[0]: row[1:] for row in self.db.execute(
            'SELECT steam_id,rank,score,observed_at,global_verified FROM match_ranking_snapshots '
            'WHERE match_id=? AND season=?', (match_id, season)).fetchall()}
        owner_start = start_rows.get(owner_id)
        ended_at = (record.get('played_at_ns') or 0) / 1_000_000_000
        actual_started = float(record.get('replay_started_at') or live_started)
        if not reason and (not owner_start or owner_start[1] is None or
                           not live_started or owner_start[2] < actual_started - 60 or
                           (ended_at and owner_start[2] >= ended_at)):
            reason = 'Нет подтверждённого снимка рейтинга владельца при начале матча.'
        now = time.time()
        if reason:
            self._set_match_rating_change(match_id, season, owner_id, 'unavailable',
                                          before_rank=owner_start[0] if owner_start else None,
                                          before_score=owner_start[1] if owner_start else None,
                                          started_at=now, deadline=now, message=reason)
            return
        ended_at = min(ended_at or now, now)
        self._set_match_rating_change(match_id, season, owner_id, 'pending',
                                      before_rank=owner_start[0], before_score=owner_start[1],
                                      started_at=ended_at,
                                      deadline=ended_at + RATING_CONFIRMATION_SECONDS,
                                      calculation_version=2,
                                      message='Ждём обновления таблицы Steam.')
        self.rating_wake.set()
        latest = self.db.execute(
            'SELECT rank,score,total,observed_at FROM ranking_snapshots '
            'WHERE season=? AND steam_id=? AND observed_at>=? ORDER BY observed_at DESC LIMIT 1',
            (season, owner_id, ended_at)).fetchone()
        if latest:
            self.confirm_pending_rating_from_refresh(season, {
                'status': 'ok', 'checked_at': datetime.fromtimestamp(latest[3], timezone.utc).isoformat(),
                'players': {owner_id: {'rank': latest[0], 'score': latest[1]}}}, [owner_id])

    def import_file(self, path):
        from forts_replay import header_length, read_replay_winner
        with path.open('rb') as stream:
            prefix = stream.read(17)
            data = prefix + stream.read(header_length(prefix) + 4 - len(prefix))
        winner, outcome = read_replay_winner(path)
        stat = path.stat()
        result = self.add(data, path.name, str(path), winner, outcome,
                          _file_hash(path), stat.st_mtime_ns,
                          started_at=_replay_started_at(path, path.name))
        if result[1]:
            self.log_seen.pop(str(path.parent.parent / 'log.txt'), None)
        return result

    def matches(self):
        with self.lock:
            matches = [json.loads(row[0]) for row in self.db.execute('SELECT data FROM matches')]
        return sorted(matches, key=lambda record: (record.get('played_at_ns') or 0,
                                                    record.get('date') or '',
                                                    record.get('imported_at') or 0), reverse=True)

    def active_season(self):
        return current_season()

    def snapshot(self, since=None):
        with self.lock:
            active_season = self.active_season()
            last_owner_rating = None
            if self.owner_steam_id:
                row = self.db.execute(
                    'SELECT rank, score, total, observed_at FROM ranking_snapshots '
                    'WHERE season=? AND steam_id=? ORDER BY observed_at DESC LIMIT 1',
                    (active_season, self.owner_steam_id)).fetchone()
                if row:
                    last_owner_rating = {'rank': row[0], 'score': row[1], 'total': row[2], 'observed_at': row[3]}
            owner_rank_gap = None
            if self.owner_steam_id:
                row = self.db.execute(
                    'SELECT data FROM leaderboard_gaps WHERE season=? AND steam_id=?',
                    (active_season, self.owner_steam_id)).fetchone()
                if row:
                    owner_rank_gap = json.loads(row[0])
            out = {'revision': str(self.revision), 'watch': dict(self.config), 'status': dict(self.status),
                   'count': self.db.execute('SELECT COUNT(*) FROM matches').fetchone()[0],
                   'language': self.language,
                   'rating_tracking': dict(self.rating_status),
                   'owner_steam_id': self.owner_steam_id,
                   'owner_mode': self.owner_mode, 'accounts': self.accounts(),
                   'active_season': active_season,
                   'season_end': season_end_date(active_season).isoformat(),
                   'owner_last_rating': last_owner_rating,
                   'owner_rank_gap': owner_rank_gap,
                   'onboarding_complete': bool(self.db.execute(
                       "SELECT value FROM settings WHERE key='onboarding_complete'"
                   ).fetchone())}
            if str(since) != str(self.revision):
                out['matches'] = [self._with_historical_ranks(record) for record in self.matches()]
            return out

    @staticmethod
    def _valid_owner_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{17}', value):
            return False
        try:
            from forts_replay import valid_steam_id
            return valid_steam_id(int(value))
        except (ImportError, ValueError):
            return False

    @classmethod
    def _owner_id_from_folders(cls, folders):
        for folder in folders:
            match = re.search(r'(?:^|[\\/])users[\\/](\d{17})[\\/]replays[\\/]?$', str(folder), re.I)
            if match and cls._valid_owner_id(match[1]):
                return match[1]
        return ''

    @classmethod
    def _match_owner_id(cls, record):
        source = record.get('source')
        source_id = cls._owner_id_from_folders([str(Path(source).parent)]) if source else ''
        participant_ids = {p.get('steam_id') for p in record.get('players', [])
                           if cls._valid_owner_id(p.get('steam_id'))}
        if source_id and source_id in participant_ids:
            return source_id
        recorder = record.get('recorder_client_id')
        if recorder is not None:
            player = next((p for p in record.get('players', [])
                           if str(p.get('client_id')) == str(recorder)), None)
            steam_id = player.get('steam_id') if player else None
            if cls._valid_owner_id(steam_id):
                return steam_id
        return source_id

    def _latest_match_owner_id(self):
        rows = self.db.execute('SELECT data FROM matches').fetchall()
        if not rows:
            return ''
        records = [json.loads(row[0]) for row in rows]
        latest = max(records, key=lambda record: (
            int(record.get('played_at_ns') or 0),
            float(record.get('match_epoch') or 0),
            int(record.get('imported_at') or 0)))
        return self._match_owner_id(latest)

    def _sync_owner_to_latest_match(self):
        if self.owner_mode == 'manual':
            return
        owner = self._latest_match_owner_id()
        if not owner or owner == self.owner_steam_id:
            return
        self.owner_steam_id = owner
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('owner_steam_id',?)", (owner,))
        self.db.commit()
        self.revision += 1

    def accounts(self):
        with self.lock:
            ids = {self._match_owner_id(record) for record in self.matches()}
            ids.update(self._owner_id_from_folders([folder]) for folder in self.config.get('folders', []))
            saved = self.db.execute("SELECT value FROM settings WHERE key='known_accounts'").fetchone()
            if saved:
                try:
                    ids.update(json.loads(saved[0]))
                except (ValueError, TypeError):
                    pass
            ids.add(self.owner_steam_id)
            return sorted(sid for sid in ids if self._valid_owner_id(sid))

    def select_account(self, mode, steam_id=''):
        if mode not in ('auto', 'manual') or (mode == 'manual' and not self._valid_owner_id(steam_id)):
            raise ValueError('Укажи корректный Steam ID64 и режим выбора аккаунта.')
        with self.lock:
            known = self.accounts()
            if mode == 'manual':
                known = sorted(set(known) | {steam_id})
                self.owner_steam_id = steam_id
            else:
                self.owner_steam_id = (self._latest_match_owner_id() or
                                      self._owner_id_from_folders(self.config.get('folders', [])) or
                                      self.owner_steam_id)
            self.owner_mode = mode
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [
                ('owner_mode', mode), ('owner_steam_id', self.owner_steam_id),
                ('known_accounts', json.dumps(known))])
            self.db.commit()
            self.revision += 1
        return self.owner_steam_id

    def set_owner_id(self, steam_id):
        steam_id = (steam_id or '').strip()
        if steam_id and not self._valid_owner_id(steam_id):
            raise ValueError('Укажи корректный Steam ID64 из 17 цифр.')
        if self.owner_mode == 'manual':
            return self.select_account('manual' if steam_id else 'auto', steam_id)
        with self.lock:
            if steam_id:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES ('owner_steam_id',?)", (steam_id,))
                self.owner_steam_id = steam_id
            else:
                self.db.execute("DELETE FROM settings WHERE key='owner_steam_id'")
                self.owner_steam_id = (self._latest_match_owner_id() or
                                       self._owner_id_from_folders(self.config.get('folders', [])))
            self.db.commit()
            self.revision += 1
        return self.owner_steam_id

    def set_onboarding_complete(self, complete=True):
        with self.lock:
            if complete:
                self.db.execute(
                    "INSERT OR REPLACE INTO settings VALUES ('onboarding_complete','1')")
            else:
                self.db.execute("DELETE FROM settings WHERE key='onboarding_complete'")
            self.db.commit()
            self.revision += 1

    def set_language(self, language):
        language = (language or '').strip().lower()
        if language not in ('ru', 'en'):
            raise ValueError('Выбери русский или английский язык интерфейса.')
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('language',?)", (language,))
            self.db.commit()
            self.language = language
            self.revision += 1
        return self.language

    def _with_historical_ranks(self, record):
        stamp = (record.get('replay_started_at') or record.get('match_epoch')
                 or _match_epoch(record.get('date', '')))
        result = dict(record)
        found = {}
        season = int(record.get('season') or 0)
        match_id = record.get('match_id')
        for player in record.get('players', []):
            steam_id = player.get('steam_id')
            if not steam_id:
                continue
            direct = self.db.execute(
                'SELECT rank, score, total, observed_at FROM match_ranking_snapshots '
                'WHERE match_id=? AND steam_id=?', (match_id, steam_id)).fetchone()
            if direct:
                found[steam_id] = {'rank': direct[0], 'score': direct[1], 'total': direct[2],
                                   'observed_at': direct[3], 'age_seconds': 0, 'at_match_start': True}
            elif stamp is not None:
                row = self.db.execute(
                    'SELECT rank, score, total, observed_at FROM ranking_snapshots '
                    'WHERE season=? AND steam_id=? AND observed_at<=? '
                    'ORDER BY observed_at DESC LIMIT 1',
                    (season, steam_id, stamp)).fetchone()
                if row:
                    found[steam_id] = {'rank': row[0], 'score': row[1], 'total': row[2],
                                       'observed_at': row[3], 'age_seconds': max(0, stamp - row[3])}
        result['historical_ranks'] = found
        rating = self.db.execute(
            'SELECT steam_id,status,before_rank,before_score,after_rank,after_score,delta,observed_at,message,global_verified,calculation_version '
            'FROM match_rating_changes WHERE match_id=?', (match_id,)).fetchone()
        if rating:
            result['rating_change'] = {
                'steam_id': rating[0], 'status': rating[1], 'before_rank': rating[2],
                'before_score': rating[3], 'after_rank': rating[4], 'after_score': rating[5],
                'delta': rating[6], 'observed_at': rating[7], 'message': rating[8],
            }
            if rating[1] == 'confirmed' and not rating[9] and rating[10] < 2:
                # Retain the old evidence in SQLite, but do not advertise the
                # former first-changed-response heuristic as a verified delta.
                result['rating_change'].update(status='unavailable', delta=None,
                    message='Старый расчёт не прошёл проверку глобальной таблицы Steam.')
        else:
            result['rating_change'] = {
                'steam_id': None, 'status': 'unavailable', 'delta': None,
                'message': 'Нет подтверждённых снимков рейтинга за этот матч.',
            }
        return result

    def record_ranking_snapshot(self, season: int, leaderboard: dict, tracked_ids=None):
        if leaderboard.get('status') != 'ok' or not isinstance(leaderboard.get('players'), dict):
            return 0
        checked_at = leaderboard.get('checked_at')
        try:
            observed_at = datetime.fromisoformat(checked_at.replace('Z', '+00:00')).timestamp()
        except (AttributeError, TypeError, ValueError):
            observed_at = time.time()
        total = leaderboard.get('total')
        inserted = 0
        with self.lock:
            entries = leaderboard['players']
            for steam_id in (tracked_ids if tracked_ids is not None else entries.keys()):
                info = entries.get(steam_id)
                try:
                    rank = int(info['rank']) if info else None
                    score = int(info['score']) if info and info.get('score') is not None else None
                    if (rank is not None and rank < 1) or len(steam_id) != 17 or not steam_id.isdigit():
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                cursor = self.db.execute(
                    'INSERT OR IGNORE INTO ranking_snapshots(season,steam_id,observed_at,rank,score,total) VALUES (?,?,?,?,?,?)',
                    (int(season), steam_id, observed_at, rank, score, total))
                inserted += cursor.rowcount
                # Confirmed match deltas are historical facts. Later matches
                # change the player's current score, but must not rewrite the
                # already verified before/after scores for an earlier match.
            self.db.commit()
            self.confirm_pending_rating_from_refresh(season, leaderboard, tracked_ids)
        return inserted

    def confirm_pending_rating_from_refresh(self, season: int, leaderboard: dict, tracked_ids=None):
        """Resolve a game's score difference from any Steam response, without a timer.

        Another game's start is a useful boundary, not a reason to discard the
        result. Only another completed game can make an unsplit difference
        ambiguous. Keep every confirmed difference attached to its own replay.
        """
        if leaderboard.get('status') != 'ok' or not isinstance(leaderboard.get('players'), dict):
            return 0
        checked_at = leaderboard.get('checked_at')
        try:
            observed_at = datetime.fromisoformat(checked_at.replace('Z', '+00:00')).timestamp()
        except (AttributeError, TypeError, ValueError):
            return 0
        entries = leaderboard['players']
        tracked = set(tracked_ids or entries.keys())
        confirmed = 0
        with self.lock:
            records = {match_id: json.loads(raw) for match_id, raw in
                       self.db.execute('SELECT id,data FROM matches').fetchall()}
            pending = self.db.execute(
                "SELECT match_id,steam_id,before_rank,before_score,started_at,deadline,status,message,observed_at "
                "FROM match_rating_changes WHERE season=? AND (status IN ('pending','unavailable') "
                "OR (status='confirmed' AND delta=0)) ORDER BY started_at",
                (int(season),)).fetchall()
            for match_id, steam_id, before_rank, before_score, started_at, deadline, state, message, last_observed in pending:
                entry = entries.get(steam_id)
                recoverable = message in (
                    'Steam не подтвердил обновление рейтинга в течение 10 минут.',
                    'Следующий матч начался до подтверждения обновления рейтинга.',
                    'Счёт Steam пока не изменился после матча.')
                if (steam_id not in tracked or not entry or
                        before_score is None or observed_at < started_at or
                        (state == 'unavailable' and not recoverable) or
                        (state == 'confirmed' and (observed_at <= (last_observed or 0) or observed_at > deadline))):
                    continue
                try:
                    after_score = int(entry['score'])
                    after_rank = int(entry['rank'])
                except (KeyError, TypeError, ValueError):
                    continue
                if after_rank < 1:
                    continue
                record = records.get(match_id)
                start_row = self.db.execute(
                    'SELECT rank,score,observed_at FROM match_ranking_snapshots '
                    'WHERE match_id=? AND season=? AND steam_id=?',
                    (match_id, int(season), steam_id)).fetchone()
                if not record or not start_row or start_row[1] is None:
                    continue
                if record.get('outcome') in (None, 'unknown'):
                    continue
                match_started = float(record.get('replay_started_at') or record.get('live_discovered_at') or 0)
                related = sorted(
                    (other for other_id, other in records.items() if other_id != match_id
                     and int(other.get('season') or 0) == int(season)
                     and self._match_owner_id(other) == steam_id
                     and (other.get('replay_started_at') or other.get('live_discovered_at'))),
                    key=lambda other: other.get('replay_started_at') or other.get('live_discovered_at'))
                earlier = [other for other in related if
                           float(other.get('replay_started_at') or other.get('live_discovered_at')) < match_started
                           and other.get('outcome') not in (None, 'unknown')]
                # A delayed previous result can arrive during this game. Carry
                # that already resolved score forward instead of counting it twice.
                if earlier:
                    previous = self.db.execute(
                        'SELECT status,after_rank,after_score,observed_at,message FROM match_rating_changes WHERE match_id=?',
                        (earlier[-1]['match_id'],)).fetchone()
                    if previous and previous[0] == 'confirmed' and previous[2] is not None:
                        if start_row[2] < (previous[3] or 0) <= started_at:
                            before_rank, before_score = previous[1], previous[2]
                    elif previous and previous[4] == 'Несколько матчей завершились до получения нового счёта Steam.':
                        self._set_match_rating_change(
                            match_id, season, steam_id, 'unavailable', before_rank=before_rank,
                            before_score=before_score, started_at=started_at, deadline=deadline,
                            calculation_version=2,
                            message='Несколько матчей завершились до получения нового счёта Steam.')
                        continue
                later = [other for other in related if
                         match_started < float(other.get('replay_started_at') or other.get('live_discovered_at')) <= observed_at]
                resolved_at = observed_at
                boundary = None
                if later:
                    next_start = self.db.execute(
                        'SELECT rank,score,observed_at FROM match_ranking_snapshots WHERE match_id=? AND steam_id=?',
                        (later[0]['match_id'], steam_id)).fetchone()
                    next_end = (later[0].get('played_at_ns') or 0) / 1e9
                    if (next_start and next_start[0] and next_start[1] is not None
                            and started_at <= next_start[2] <= observed_at
                            and (later[0].get('outcome') in (None, 'unknown') or next_start[2] < next_end)
                            and next_start[1] != before_score):
                        boundary = next_start
                if boundary:
                    after_rank, after_score, resolved_at = boundary
                elif any(other.get('outcome') not in (None, 'unknown') and
                         (other.get('played_at_ns') or 0) / 1e9 <= observed_at for other in later):
                    # The recorded zero is an actual post-game observation.
                    # Once another game ends, keep it instead of attributing
                    # that newer game's score change to the earlier replay.
                    if state == 'confirmed':
                        continue
                    self._set_match_rating_change(
                        match_id, season, steam_id, 'unavailable', before_rank=before_rank,
                        before_score=before_score, started_at=started_at, deadline=deadline,
                        calculation_version=2,
                        message='Несколько матчей завершились до получения нового счёта Steam.')
                    continue
                if state == 'confirmed' and after_score == before_score:
                    continue
                current = self.db.execute(
                    'SELECT status FROM match_rating_changes WHERE match_id=?', (match_id,)
                ).fetchone()
                if not current or current[0] != state:
                    continue
                self._set_match_rating_change(
                    match_id, int(season), steam_id, 'confirmed',
                    before_rank=before_rank, before_score=before_score,
                    after_rank=after_rank, after_score=after_score,
                    delta=after_score - before_score, started_at=started_at,
                    deadline=deadline, observed_at=resolved_at,
                    global_verified=bool(entry.get('global_verified')), calculation_version=2,
                    message=f'{before_score} → {after_score}.')
                confirmed += state != 'confirmed'
            # A fresh correction for the same most recent game can replace its
            # displayed difference, while retaining every original score in
            # ranking_snapshots. Once another game starts, freeze that history.
            for steam_id in tracked:
                entry = entries.get(steam_id) or {}
                score, rank = self._safe_int(entry.get('score')), self._safe_int(entry.get('rank'))
                if score is None or rank is None or rank < 1:
                    continue
                previous = self.db.execute(
                    "SELECT match_id,before_rank,before_score,after_score,started_at,deadline,observed_at,calculation_version "
                    "FROM match_rating_changes WHERE season=? AND steam_id=? AND status='confirmed' "
                    "ORDER BY started_at DESC LIMIT 1", (int(season), steam_id)).fetchone()
                if not previous:
                    continue
                change_id, before_rank, before_score, old_after, started_at, deadline, last_observed, version = previous
                record = records.get(change_id)
                if (not record or version < 2 or before_score is None or
                        score in (before_score, old_after) or observed_at <= (last_observed or 0) or
                        observed_at > deadline):
                    continue
                start = float(record.get('replay_started_at') or record.get('live_discovered_at') or 0)
                later = any(
                    other_id != change_id and self._match_owner_id(other) == steam_id
                    and start < float(other.get('replay_started_at') or other.get('live_discovered_at') or 0) <= observed_at
                    for other_id, other in records.items())
                if not later:
                    self._set_match_rating_change(
                        change_id, season, steam_id, 'confirmed', before_rank=before_rank, before_score=before_score,
                        after_rank=rank, after_score=score, delta=score - before_score,
                        started_at=started_at, deadline=deadline, observed_at=observed_at,
                        global_verified=bool(entry.get('global_verified')), calculation_version=2,
                        message=f'{before_score} → {score}.')
        return confirmed

    def record_leaderboard_gap(self, season, steam_id, entry, checked_at=None):
        if not steam_id or not isinstance(entry, dict):
            return
        try:
            rank = int(entry['rank'])
            score = int(entry['score'])
            status = entry.get('leader_above_status', 'unavailable')
            above = entry.get('leader_above') or {}
            gap = {
                'rank': rank, 'score': score, 'status': status,
                'leader_above': ({'rank': int(above['rank']), 'score': int(above['score'])}
                                 if status == 'ok' and above.get('rank') is not None and above.get('score') is not None
                                 else None),
                'points_to_overtake': (int(entry['points_to_overtake'])
                                       if status == 'ok' and entry.get('points_to_overtake') is not None
                                       else None),
                'checked_at': checked_at,
            }
            if status not in ('ok', 'top'):
                gap['status'] = 'unavailable'
        except (KeyError, TypeError, ValueError):
            return
        with self.lock:
            self.db.execute(
                'INSERT OR REPLACE INTO leaderboard_gaps(season,steam_id,data) VALUES (?,?,?)',
                (int(season), steam_id, json.dumps(gap, ensure_ascii=True)))
            self.db.commit()
            self.revision += 1

    def _capture_ratings(self):
        """Refresh ranks for the owner and participants in the latest match."""
        with self.lock:
            rows = [json.loads(raw) for (raw,) in self.db.execute('SELECT data FROM matches')]
            active_season = self.active_season()
        if rows:
            latest = max(rows, key=lambda row: (row.get('played_at_ns') or 0,
                                                row.get('imported_at') or 0))
            ids = sorted({player['steam_id'] for player in latest.get('players', [])
                          if player.get('steam_id')})
        else:
            ids = []
        if self.owner_steam_id:
            ids = sorted(set(ids) | {self.owner_steam_id})
        if not ids:
            return
        with self.lock:
            self.rating_status.update(status='checking', message=None)
        try:
            from steam_data import rankings
            for start in range(0, len(ids), 64):
                if self.stop_event.is_set():
                    return
                result = rankings(active_season, ids[start:start + 64])
                self.record_ranking_snapshot(active_season, result, ids[start:start + 64])
                if result.get('status') != 'ok':
                    raise RuntimeError(result.get('message') or 'Steam не вернул таблицу рейтинга.')
            with self.lock:
                self.rating_status.update(last_checked=datetime.now(timezone.utc).isoformat(),
                                          status='ok', message=None)
        except Exception as exc:
            with self.lock:
                self.rating_status.update(last_checked=datetime.now(timezone.utc).isoformat(),
                                          status='error', message=str(exc))

    def _capture_live_match_ratings(self):
        """Save real score rows, retrying incomplete responses on the next tick."""
        from game_process import forts_running
        from steam_data import rankings
        if not forts_running():
            return
        with self.lock:
            pending = list(self.live_rating_pending.items())
        for match_id, (season, ids) in pending:
            if self.stop_event.is_set():
                return
            with self.lock:
                raw = self.db.execute('SELECT data FROM matches WHERE id=?', (match_id,)).fetchone()
                if not raw:
                    self.live_rating_pending.pop(match_id, None)
                    continue
                record = json.loads(raw[0])
                owner_id = self._match_owner_id(record)
                existing = self.db.execute(
                    'SELECT score FROM match_ranking_snapshots WHERE match_id=? AND steam_id=?',
                    (match_id, owner_id)).fetchone()
                captured = {row[0] for row in self.db.execute(
                    'SELECT steam_id FROM match_ranking_snapshots WHERE match_id=? AND score IS NOT NULL',
                    (match_id,)).fetchall()}
                finished = record.get('outcome') not in (None, 'unknown')
                if set(ids) <= captured or (finished and existing and existing[0] is not None):
                    self.live_rating_pending.pop(match_id, None)
                    if finished:
                        self._queue_finished_match_rating(match_id, record)
                    continue
                # Never label a fresh post-game score as the game's baseline.
                if record.get('outcome') not in (None, 'unknown'):
                    actual_start = record.get('replay_started_at') or record.get('live_discovered_at')
                    old = self.db.execute(
                        'SELECT rank,score,total,observed_at FROM ranking_snapshots '
                        'WHERE season=? AND steam_id=? AND observed_at<=? AND observed_at>=? '
                        'AND score IS NOT NULL ORDER BY observed_at DESC LIMIT 1',
                        (season, owner_id, actual_start, actual_start - 60)).fetchone()
                    if old:
                        self.db.execute(
                            'INSERT OR REPLACE INTO match_ranking_snapshots '
                            '(match_id,season,steam_id,observed_at,rank,score,total) VALUES (?,?,?,?,?,?,?)',
                            (match_id, season, owner_id, old[3], old[0], old[1], old[2]))
                        self.db.commit()
                    self.live_rating_pending.pop(match_id, None)
                    self._queue_finished_match_rating(match_id, record)
                    continue
                newer = any(
                    other_id != match_id and
                    float(json.loads(other_raw).get('live_discovered_at') or 0) >
                    float(record.get('live_discovered_at') or 0)
                    for other_id, other_raw in self.db.execute('SELECT id,data FROM matches').fetchall())
                if newer:
                    self.live_rating_pending.pop(match_id, None)
                    continue
                self.rating_status.update(status='checking', message='Сохраняем рейтинг участников нового матча…')
            try:
                result = rankings(season, ids, force=True)
                if result.get('status') != 'ok' or result.get('paused'):
                    continue
                checked_at = result.get('checked_at')
                try:
                    observed_at = datetime.fromisoformat(checked_at.replace('Z', '+00:00')).timestamp()
                except (AttributeError, TypeError, ValueError):
                    continue
                players = result.get('players') or {}
                with self.lock:
                    current_raw = self.db.execute('SELECT data FROM matches WHERE id=?', (match_id,)).fetchone()
                    current_record = json.loads(current_raw[0]) if current_raw else record
                    ended = (current_record.get('played_at_ns') or 0) / 1e9
                    can_save = current_record.get('outcome') in (None, 'unknown') or (ended and observed_at < ended)
                    if can_save:
                        for steam_id in ids:
                            entry = players.get(steam_id) or {}
                            rank, score = self._safe_int(entry.get('rank')), self._safe_int(entry.get('score'))
                            if rank is None or rank < 1 or score is None:
                                continue
                            self.db.execute(
                                'INSERT INTO match_ranking_snapshots '
                                '(match_id,season,steam_id,observed_at,rank,score,total,global_verified) VALUES (?,?,?,?,?,?,?,?) '
                                'ON CONFLICT(match_id,steam_id) DO UPDATE SET '
                                'observed_at=excluded.observed_at,rank=excluded.rank,score=excluded.score,'
                                'total=excluded.total,global_verified=excluded.global_verified '
                                'WHERE match_ranking_snapshots.score IS NULL',
                                (match_id, season, steam_id, observed_at, rank, score, result.get('total'),
                                 int(bool(entry.get('global_verified')))))
                        self.db.commit()
                        self.revision += 1
                        captured = {row[0] for row in self.db.execute(
                            'SELECT steam_id FROM match_ranking_snapshots WHERE match_id=? AND score IS NOT NULL',
                            (match_id,)).fetchall()}
                        if set(ids) <= captured:
                            self.live_rating_pending.pop(match_id, None)
                    # Saving the next baseline may also resolve a previous game.
                    self.record_ranking_snapshot(season, result, ids)
                    if current_record.get('outcome') not in (None, 'unknown'):
                        self._queue_finished_match_rating(match_id, current_record)
                    self.rating_status.update(last_checked=datetime.now(timezone.utc).isoformat(),
                                              status='ok', message=None)
            except Exception as exc:
                with self.lock:
                    self.rating_status.update(status='error', message=str(exc))

    def _capture_finished_match_ratings(self):
        """One fresh lookup per season; the scheduler retries every five seconds."""
        from game_process import forts_running
        from steam_data import rankings
        if not forts_running() or self.stop_event.is_set():
            return
        with self.lock:
            pending = self.db.execute(
                "SELECT match_id,season,steam_id,before_rank,before_score,started_at,deadline "
                "FROM match_rating_changes WHERE status='pending' "
                "OR (status='confirmed' AND delta=0 AND calculation_version>=2 AND deadline>?) "
                "ORDER BY started_at", (time.time(),)).fetchall()
        groups = {}
        for row in pending:
            match_id, season, owner_id, before_rank, before_score, started_at, deadline = row
            if time.time() >= deadline:
                self._finish_rating_unavailable(row, 'Счёт Steam пока не изменился после матча.')
                continue
            if self._valid_owner_id(owner_id):
                groups.setdefault(season, set()).add(owner_id)
        for season, owner_ids in groups.items():
            if self.stop_event.is_set():
                return
            try:
                result = rankings(season, sorted(owner_ids), force=True)
                if result.get('status') == 'ok' and not result.get('paused'):
                    self.record_ranking_snapshot(season, result, owner_ids)
                    with self.lock:
                        self.rating_status.update(last_checked=result.get('checked_at'), status='ok', message=None)
            except Exception as exc:
                with self.lock:
                    self.rating_status.update(status='error', message=str(exc))

    @staticmethod
    def _safe_int(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _finish_rating_unavailable(self, row, message):
        match_id, season, owner_id, before_rank, before_score, started_at, deadline = row
        with self.lock:
            current = self.db.execute(
                'SELECT status FROM match_rating_changes WHERE match_id=?', (match_id,)
            ).fetchone()
            if current and current[0] == 'pending':
                self._set_match_rating_change(
                    match_id, season, owner_id, 'unavailable', before_rank=before_rank,
                    before_score=before_score, started_at=started_at, deadline=deadline,
                    message=message)

    def configure(self, enabled, folders):
        if not isinstance(enabled, bool) or not isinstance(folders, list) or len(folders) > 12:
            raise ValueError('Некорректные настройки папок.')
        clean = []
        for folder in folders:
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError('Укажите полный путь к папке реплеев.')
            p = Path(folder).expanduser()
            if not p.is_absolute() or not p.is_dir():
                raise ValueError(f'Папка не найдена: {folder}')
            clean.append(str(p.resolve()))
        config = {'enabled': enabled, 'folders': list(dict.fromkeys(clean))}
        with self.lock:
            self.config = config
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('watch',?)", (json.dumps(config),))
            explicit_owner = self.db.execute("SELECT value FROM settings WHERE key='owner_steam_id'").fetchone()
            latest_owner = self._latest_match_owner_id()
            if self.owner_mode == 'auto':
                self.owner_steam_id = (latest_owner or
                                       (explicit_owner[0] if explicit_owner and self._valid_owner_id(explicit_owner[0]) else
                                        self._owner_id_from_folders(clean)))
            if self.owner_mode == 'auto' and latest_owner and (not explicit_owner or explicit_owner[0] != latest_owner):
                self.db.execute("INSERT OR REPLACE INTO settings VALUES ('owner_steam_id',?)", (latest_owner,))
            self.db.commit()
            self.revision += 1

    def encounters(self, own_id, opponent_id, exclude=None):
        found = []
        for match in self.matches():
            own = next((p for p in match['players'] if p['steam_id'] == own_id), None)
            other = next((p for p in match['players'] if p['steam_id'] == opponent_id), None)
            if own and other and own['side'] and other['side'] and own['side'] != other['side']:
                found.append({'match_id': match['match_id'], 'date': match['date'], 'map': match['map'],
                              'file': match['file'], 'name': other['name'], 'season': match.get('season'),
                              'winner_side': match.get('winner_side'), 'outcome': match.get('outcome'),
                              'own_side': own.get('side')})
        return {'total': len(found), 'matches': [m for m in found if m['match_id'] != exclude]}

    def sync_commander_log(self, folder: str):
        user_dir = Path(folder).parent
        # Forts also copies its session log into desync reports. Those copies
        # can recover commanders after the live log.txt has been overwritten.
        logs = sorted(user_dir.glob('desyncs/**/*Log*.txt')) + [user_dir / 'log.txt']
        commanders = {}
        for log in logs:
            try:
                stat = log.stat()
                fingerprint = (stat.st_size, stat.st_mtime_ns)
                if stat.st_size > 32 * 1024 * 1024:
                    continue
                key = str(log)
                if self.log_seen.get(key) != fingerprint:
                    self.log_commanders[key] = _commanders_from_log(log)
                    self.log_seen[key] = fingerprint
                commanders.update(self.log_commanders[key])
            except OSError:
                continue
        if not commanders:
            return
        with self.lock:
            for match_id, raw in self.db.execute('SELECT id, data FROM matches').fetchall():
                record = json.loads(raw)
                source = record.get('source')
                if not source or Path(source).parent != Path(folder):
                    continue
                chosen = commanders.get(record.get('file', '').casefold())
                if chosen and chosen != record.get('commanders'):
                    record['commanders'] = chosen
                    self.db.execute('UPDATE matches SET data=? WHERE id=?',
                                    (json.dumps(record, ensure_ascii=True), match_id))
                    self.revision += 1
            self.db.commit()

    def scan_once(self):
        with self.lock:
            config = dict(self.config)
        if not config['enabled']:
            return
        folder_errors, replay_errors, checked = [], [], 0
        with self.lock:
            self.status['scanning'] = True
        active = set()
        for folder in config['folders']:
            directory = Path(folder)
            if not directory.is_dir():
                folder_errors.append(f'Папка недоступна: {folder}')
                continue
            try:
                paths = list(directory.glob('*.fwr'))
            except OSError as exc:
                folder_errors.append(str(exc))
                continue
            for path in paths:
                if self.stop_event.is_set():
                    break
                key = str(path)
                active.add(key)
                if not is_ranked_replay(path.name):
                    self.seen[key] = None
                    continue
                try:
                    stat = path.stat()
                    fingerprint = (stat.st_size, stat.st_mtime_ns)
                    checked += 1
                    # A Forts replay header is written near game start; the body
                    # keeps growing until the match ends. Parse that fixed-size
                    # header immediately instead of waiting for the whole replay
                    # file to become stable.
                    if key not in self.header_seen:
                        from forts_replay import header_length
                        with path.open('rb') as stream:
                            prefix = stream.read(17)
                            length = header_length(prefix)
                            if stat.st_size >= length + 4:
                                header = prefix + stream.read(length + 4 - len(prefix))
                            else:
                                header = None
                        if header is not None:
                            self.header_seen.add(key)
                            if abs(time.time() - stat.st_mtime) <= 30:
                                self.add(header, path.name, str(path), outcome='unknown',
                                         played_at_ns=stat.st_mtime_ns, live=True,
                                         started_at=stat.st_ctime)
                    if self.seen.get(key) == fingerprint:
                        continue
                    # Wait for the same size/mtime across two scans. A replay may
                    # still be being written when it first appears in the folder.
                    pending = self.pending.get(key)
                    if not pending or pending != fingerprint:
                        self.pending[key] = fingerprint
                        continue
                    if stat.st_size < 17:
                        continue
                    self.import_file(path)
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) == fingerprint:
                        self.seen[key] = fingerprint
                except (OSError, ValueError) as exc:
                    # Retry on later scans, including files completed in place.
                    if len(replay_errors) < 5:
                        replay_errors.append(f'{path.name}: {exc}')
        self.pending = {k: v for k, v in self.pending.items() if k in active}
        self.seen = {k: v for k, v in self.seen.items() if k in active}
        self.header_seen.intersection_update(active)
        for folder in config['folders']:
            self.sync_commander_log(folder)
        with self.lock:
            self.status = {'scanning': False, 'checked': checked,
                           'errors': (folder_errors + replay_errors)[:5],
                           'folder_errors': folder_errors, 'replay_errors': replay_errors,
                           'last_scan': time.time()}

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    self.scan_once()
                except Exception as exc:
                    with self.lock:
                        self.status.update(scanning=False, errors=[str(exc)],
                                           folder_errors=[], replay_errors=[])
                self.stop_event.wait(1)
        self.thread = threading.Thread(target=run, name='replay-watcher', daemon=True)
        self.thread.start()

        def collect_ratings():
            from game_process import forts_running
            last_regular_check = 0
            while not self.stop_event.is_set():
                self.rating_wake.wait(RATING_CONFIRMATION_INTERVAL_SECONDS)
                self.rating_wake.clear()
                if self.stop_event.is_set():
                    break
                if not forts_running():
                    with self.lock:
                        self.rating_status.update(status='paused', message='Forts закрыта. Проверка рейтинга приостановлена.')
                    last_regular_check = 0
                    continue
                self._capture_live_match_ratings()
                self._capture_finished_match_ratings()
                if not self.stop_event.is_set() and time.monotonic() - last_regular_check >= 60:
                    self._capture_ratings()
                    last_regular_check = time.monotonic()
        self.rating_thread = threading.Thread(target=collect_ratings, name='rating-history', daemon=True)
        self.rating_thread.start()

    def close(self):
        self.stop_event.set()
        self.rating_wake.set()
        if self.thread:
            self.thread.join(timeout=10)
        if self.rating_thread:
            self.rating_thread.join(timeout=25)
        with self.lock:
            self.db.close()
