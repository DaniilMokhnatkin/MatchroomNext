"""Ordinary (non-ranked) lobby games, counted automatically from replays.

Ranked history keeps its own database and rating logic; this store only reads
replays whose name has no season prefix. Each game keeps the roster, our side,
the winner and the length, so the client can build records by mode, map,
teammate and opponent. The replay files are never modified.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from forts_replay import HEADER_END, ReplayError, _last_state_block, header_length, parse_replay
from match_history import is_ranked_replay
from replay_files import SCAN_INTERVAL, file_played_at_ns, filename_epoch, ready_for_analysis, unchanged_replay

STAMP = re.compile(r'_(20\d{6}_\d{6})')
BATCH = 40


def game_facts(path: Path) -> dict:
    """Read roster from the header and the result from the last state block."""
    size = path.stat().st_size
    with path.open('rb') as stream:
        prefix = stream.read(17)
        length = header_length(prefix)
        header = prefix + stream.read(length + 4 - len(prefix))
        state = None
        for tail_size in (2 * 1024 * 1024, 16 * 1024 * 1024):
            tail_size = min(size, tail_size)
            stream.seek(size - tail_size)
            state = _last_state_block(stream.read(tail_size), size - tail_size, size)
            if state is not None or tail_size == size:
                break
    if header[length:length + 4] != HEADER_END:
        raise ReplayError('Заголовок обрезан.')
    parsed = parse_replay(header, path.name)
    winner, outcome, seconds = None, 'unknown', None
    if state:
        times = re.findall(rb'gameTime = ([0-9.]+),', state)
        seconds = round(float(times[-1])) if times else None
        if b'gameEnded = true,' in state:
            values = re.findall(rb'gameWinner = (-?[0-9]+),', state)
            if values:
                value = int(values[-1])
                winner = value if value in (1, 2) else None
                outcome = 'winner' if winner else 'draw' if value == 0 else 'unknown'
    players = [{'name': p['name'], 'steam_id': p['steam_id'], 'side': p['side'], 'role': p['role']}
               for p in parsed['players']]
    own = next((p for p in players if p['role'] == 'self'), None)
    sides = {1: 0, 2: 0}
    for p in players:
        if p['side'] in sides and p['role'] != 'observer':
            sides[p['side']] += 1
    if own is None or own['role'] == 'observer' or not own['side']:
        result = 'observer'
    elif outcome == 'winner':
        result = 'win' if winner == own['side'] else 'loss'
    else:
        result = 'draw' if outcome == 'draw' else 'unknown'
    stamp = STAMP.search(path.name)
    humans = sum(1 for p in players if p['steam_id'] and p['role'] != 'observer')
    return {
        # Skirmishes against AI and sandbox sessions carry only our own seat.
        'multiplayer': humans >= 2, 'humans': humans,
        'file': path.name, 'date': stamp[1] if stamp else '', 'map': parsed.get('map') or '',
        'mode': f'{sides[1]}v{sides[2]}' if sides[1] and sides[2] else 'other',
        'players': players, 'self_id': own['steam_id'] if own else None,
        'my_side': own['side'] if own else None, 'winner_side': winner, 'result': result,
        'duration': seconds,
        'path': str(path), 'played_at_ns': file_played_at_ns(path),
    }


class CasualGames:
    def __init__(self, directory, folders=lambda: [], enabled=lambda: True):
        self.directory = Path(directory)
        self.folders, self.enabled = folders, enabled
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'casual.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS games (key TEXT PRIMARY KEY, date TEXT NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS skipped (key TEXT PRIMARY KEY, reason TEXT NOT NULL)')
        self.db.commit()
        self.lock = threading.RLock()
        self._repair_times()
        self.known = {row[0] for row in self.db.execute('SELECT key FROM games UNION SELECT key FROM skipped')}
        self.revision = str(time.time_ns())
        self.status = {'scanning': False, 'pending': 0, 'imported': len(self.known), 'errors': 0, 'last_scan': None}
        self.stop_event = threading.Event()
        self.thread = None

    @staticmethod
    def key(path: Path, size: int) -> str:
        return hashlib.sha1(f'{path.name.casefold()}|{size}'.encode('utf-8')).hexdigest()

    def _repair_times(self):
        """Fill timing metadata for cached games without parsing them again."""
        folders = [Path(folder) for folder in self.folders() or []]
        for key, raw in self.db.execute('SELECT key, data FROM games').fetchall():
            game = json.loads(raw)
            if game.get('played_at_ns') or not game.get('file'):
                continue
            paths = ([Path(game['path'])] if game.get('path') else []) + [p / game['file'] for p in folders]
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    game.update(played_at_ns=file_played_at_ns(path), path=str(path))
                except OSError:
                    continue
                self.db.execute('UPDATE games SET data=? WHERE key=?', (json.dumps(game, ensure_ascii=True), key))
                break
        self.db.commit()

    def scan_once(self, now=None, limit=None):
        now = time.time() if now is None else now
        if not self.enabled():
            return 0
        todo = []
        for folder in self.folders() or []:
            try:
                paths = list(Path(folder).glob('*.fwr'))
            except OSError:
                continue
            for path in paths:
                if is_ranked_replay(path.name):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = self.key(path, stat.st_size)
                if key not in self.known and ready_for_analysis(path, stat, now):
                    todo.append((stat.st_mtime, key, path, stat))
        # Newest first, so the latest evenings appear before the old archive.
        todo.sort(reverse=True)
        pending = len(todo)
        if limit:
            todo = todo[:limit]
        with self.lock:
            self.status.update(scanning=bool(todo), pending=pending)
        added = 0
        for index, (_mtime, key, path, stat) in enumerate(todo):
            if self.stop_event.is_set():
                break
            try:
                facts = game_facts(path)
                if not unchanged_replay(path, stat):
                    continue
                facts['id'] = key
                with self.lock:
                    self.db.execute('INSERT OR REPLACE INTO games VALUES (?,?,?)',
                                    (key, facts['date'], json.dumps(facts, ensure_ascii=True)))
                added += 1
            except (OSError, ValueError) as exc:
                if not unchanged_replay(path, stat):
                    continue
                with self.lock:
                    self.db.execute('INSERT OR REPLACE INTO skipped VALUES (?,?)', (key, str(exc)[:200]))
                    self.status['errors'] += 1
            self.known.add(key)
            if (index + 1) % BATCH == 0 or index + 1 == len(todo):
                with self.lock:
                    self.db.commit()
                    self.revision = str(time.time_ns())
                    self.status.update(pending=pending - index - 1, imported=len(self.known))
        with self.lock:
            # A changed/deleted last file can bypass the per-batch commit.
            self.db.commit()
            if added:
                self.revision = str(time.time_ns())
            self.status.update(scanning=False, pending=pending - len(todo), imported=len(self.known), last_scan=time.time())
        return added

    def games(self):
        with self.lock:
            rows = [json.loads(raw) for raw, in self.db.execute('SELECT data FROM games ORDER BY date DESC')]
        return sorted(rows, key=lambda game: game.get('played_at_ns') or
                      int((filename_epoch(game.get('date', '')) or 0) * 1_000_000_000), reverse=True)

    def snapshot(self, since=None):
        with self.lock:
            out = {'revision': self.revision, 'status': dict(self.status)}
            if str(since) != self.revision:
                out['games'] = self.games()
            return out

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    added = self.scan_once(limit=4)
                except Exception:
                    logging.exception('Casual game scan failed')
                    added = 0
                self.stop_event.wait(0.1 if added else SCAN_INTERVAL)
        self.thread = threading.Thread(target=run, daemon=True, name='casual-games')
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        with self.lock:
            self.db.close()
