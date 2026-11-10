"""Everything Next knows about players, kept locally in players.sqlite3.

Sources, all optional and merged per Steam ID:
* every replay in the watched folders (ranked and lobby), decoded once by the
  bundled analyser: per-game APM, damage, economy, losses and builds;
* Steam's ranked leaderboard entries read while Forts runs, including the
  hidden details: internal rating, season wins/losses, last match time and the
  last ten opponents;
* forts-tools summaries when that source is switched on.

The dossier and match views are assembled from these tables on request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import struct
from subprocess import TimeoutExpired
import threading
import time
from datetime import datetime
from pathlib import Path

from replay_analysis import baseline, compare, form_verdict, run_fwr, summarize
from replay_files import SCAN_INTERVAL, file_played_at_ns, ready_for_analysis, unchanged_replay

STAMP = re.compile(r'_(20\d{6}_\d{6})')
SEASON = re.compile(r'^([1-9][0-9]{0,2})_')
WORKERS = 3
STEAM_ID = re.compile(r'^[0-9]{17}$')


def parse_details(values):
    """Decode a ranked leaderboard entry's details (int32 list).

    Layout observed on season boards: [unknown, 1, last match unix time,
    internal rating, season wins, season losses, 0, 0] followed by the last
    opponents as 64-bit Steam IDs (two ints each).
    """
    if not values or len(values) < 6 or values[1] != 1:
        return None
    raw = struct.pack('<%di' % len(values), *values)
    opponents = []
    for offset in range(32, len(raw) - 7, 8):
        sid = struct.unpack_from('<Q', raw, offset)[0]
        if sid >> 32 == 0x01100001 and sid & 0xFFFFFFFF:
            opponents.append(str(sid))
    internal, wins, losses = values[3], values[4], values[5]
    if not (0 < internal < 5000 and 0 <= wins < 100000 and 0 <= losses < 100000):
        return None
    return {'last_match': values[2] if values[2] > 1_000_000_000 else None, 'internal': internal,
            'wins': wins, 'losses': losses, 'opponents': opponents}


def stamp_epoch(name):
    found = STAMP.search(name or '')
    if not found:
        return None
    try:
        return datetime.strptime(found[1], '%Y%m%d_%H%M%S').timestamp()
    except ValueError:
        return None


def mode_of(players):
    sides = {1: 0, 2: 0}
    for p in players:
        if p.get('side') in sides:
            sides[p['side']] += 1
    return f'{sides[1]}v{sides[2]}' if sides[1] and sides[2] else 'other'


def mode_class(mode):
    return 'duel' if mode == '1v1' else 'team'


class PlayerDB:
    def __init__(self, directory, folders=lambda: [], enabled=lambda: True, owner=lambda: '',
                 analyse=None, clock=time.time, database=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.folders, self.enabled, self.owner = folders, enabled, owner
        self.analyse = analyse or run_fwr
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database or (self.directory / 'players.sqlite3'), check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS analyses (key TEXT PRIMARY KEY, file TEXT, path TEXT, played REAL,
                ranked INTEGER, mode TEXT, map TEXT, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS failed (key TEXT PRIMARY KEY, reason TEXT);
            CREATE TABLE IF NOT EXISTS appearances (key TEXT, steam_id TEXT, side INTEGER, won INTEGER,
                ranked INTEGER, mode TEXT, played REAL, metrics TEXT, PRIMARY KEY (key, steam_id));
            CREATE INDEX IF NOT EXISTS appearances_player ON appearances(steam_id, played DESC);
            CREATE TABLE IF NOT EXISTS names (steam_id TEXT, name TEXT, first_seen REAL, last_seen REAL,
                PRIMARY KEY (steam_id, name));
            CREATE TABLE IF NOT EXISTS ratings (steam_id TEXT, observed_at REAL, source TEXT, season INTEGER,
                rank INTEGER, score INTEGER, internal INTEGER, wins INTEGER, losses INTEGER,
                PRIMARY KEY (steam_id, observed_at, source));
            CREATE TABLE IF NOT EXISTS board (steam_id TEXT PRIMARY KEY, season INTEGER, observed_at REAL,
                data TEXT NOT NULL);
        ''')
        self.db.commit()
        self._repair_times()
        self.known = {row[0] for row in self.db.execute('SELECT key FROM analyses UNION SELECT key FROM failed')}
        self.status = {'scanning': False, 'pending': 0, 'analysed': len(self.known), 'errors': 0, 'available': True}
        self.revision = time.time_ns()
        self.stop_event = threading.Event()
        self.thread = None

    # ----- replay analysis -----
    def _repair_times(self):
        """Upgrade cached analysis dates and dossier order without rerunning FWR."""
        for key, source, raw in self.db.execute('SELECT key, path, data FROM analyses').fetchall():
            data = json.loads(raw)
            if data.get('played_at_ns'):
                continue
            path = Path(source)
            try:
                stat = path.stat()
                if self.key(path, stat.st_size) != key:
                    continue
                when = file_played_at_ns(path, stat)
            except OSError:
                continue
            data.update(played_at_ns=when, played=when / 1_000_000_000)
            self.db.execute('UPDATE analyses SET played=?, data=? WHERE key=?',
                            (data['played'], json.dumps(data, ensure_ascii=True), key))
            self.db.execute('UPDATE appearances SET played=? WHERE key=?', (data['played'], key))
        self.db.commit()

    @staticmethod
    def key(path: Path, size: int) -> str:
        return hashlib.sha1(f'{path.name.casefold()}|{size}'.encode('utf-8')).hexdigest()

    def scan_once(self, now=None, limit=None):
        now = self.clock() if now is None else now
        if not self.enabled():
            return 0
        todo = []
        for folder in self.folders() or []:
            try:
                paths = list(Path(folder).glob('*.fwr'))
            except OSError:
                continue
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = self.key(path, stat.st_size)
                if key not in self.known and ready_for_analysis(path, stat, now):
                    todo.append((stat.st_mtime, key, path, stat))
        todo.sort(reverse=True)
        pending = len(todo)
        if limit:
            todo = todo[:limit]
        with self.lock:
            self.status.update(scanning=bool(todo), pending=pending)
        added = 0
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for start in range(0, len(todo), WORKERS * 4):
                if self.stop_event.is_set():
                    break
                chunk = todo[start:start + WORKERS * 4]
                futures = {pool.submit(self._prepare, path): (key, path, stat) for _mtime, key, path, stat in chunk}
                for future in as_completed(futures):
                    key, path, stat = futures[future]
                    try:
                        result = future.result()
                        if not unchanged_replay(path, stat):
                            continue
                        self._store(key, path, *result)
                        added += 1
                    except FileNotFoundError:
                        continue
                    except RuntimeError as exc:
                        # The analyser itself is missing: stop, never mark files as failed.
                        with self.lock:
                            self.status.update(available=False, scanning=False, error=str(exc))
                        return added
                    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        if not unchanged_replay(path, stat):
                            continue
                        with self.lock:
                            self.db.execute('INSERT OR REPLACE INTO failed VALUES (?,?)', (key, str(exc)[:200]))
                            self.db.commit()
                            self.status['errors'] += 1
                        self.known.add(key)
                with self.lock:
                    self.status.update(pending=max(0, pending - start - len(chunk)), analysed=len(self.known))
        with self.lock:
            self.status.update(scanning=False, pending=pending - len(todo), analysed=len(self.known))
        return added

    def add_analysis(self, key, path: Path):
        self._store(key, path, *self._prepare(path))

    def _prepare(self, path: Path):
        names = {}
        try:
            from forts_replay import read_replay
            names = {p['steam_id']: p['name'] for p in read_replay(path)['players'] if p.get('steam_id') and p.get('name')}
        except (OSError, ValueError):
            pass
        return (summarize(self.analyse(path), names),)

    def _store(self, key, path: Path, summary):
        summary['played_at_ns'] = file_played_at_ns(path)
        played = summary['played_at_ns'] / 1_000_000_000
        self.store_summary(key, path.name, str(path), played, summary)

    def store_summary(self, key, file_name, source, played, summary):
        humans = [p for p in summary['players'] if p.get('steam_id')]
        mode = mode_of(summary['players'])
        season = SEASON.match(file_name)
        summary.update(file=file_name, played=played, mode=mode, season=int(season[1]) if season else None)
        with self.lock:
            if len(humans) < 2:
                # Skirmish against AI or a sandbox: nothing to learn about other players.
                self.db.execute('INSERT OR REPLACE INTO failed VALUES (?,?)', (key, 'solo'))
            else:
                self.db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?,?,?)',
                                (key, file_name, source, played, int(summary['ranked']), mode,
                                 summary['map'], json.dumps(summary, ensure_ascii=True)))
                for player in humans:
                    won = None if player['won'] is None else int(player['won'])
                    self.db.execute('INSERT OR REPLACE INTO appearances VALUES (?,?,?,?,?,?,?,?)',
                                    (key, player['steam_id'], player['side'], won, int(summary['ranked']),
                                     mode, played, json.dumps(player['metrics'])))
                    if player.get('name'):
                        self._name(player['steam_id'], player['name'], played)
                recorder = next((p for p in humans if p.get('recorder')), None)
                if recorder and summary.get('rating_after'):
                    self.db.execute('INSERT OR IGNORE INTO ratings VALUES (?,?,?,?,?,?,?,?,?)',
                                    (recorder['steam_id'], played, 'replay', summary.get('season'), None, None,
                                     summary['rating_after'], None, None))
            self.db.commit()
            self.known.add(key)
            self.status['analysed'] = len(self.known)
            self.revision = time.time_ns()

    def _name(self, steam_id, name, seen):
        name = str(name)[:40]
        row = self.db.execute('SELECT first_seen, last_seen FROM names WHERE steam_id=? AND name=?', (steam_id, name)).fetchone()
        if row:
            self.db.execute('UPDATE names SET first_seen=?, last_seen=? WHERE steam_id=? AND name=?',
                            (min(row[0], seen), max(row[1], seen), steam_id, name))
        else:
            self.db.execute('INSERT INTO names VALUES (?,?,?,?)', (steam_id, name, seen, seen))

    # ----- other sources -----
    def record_board(self, season, players, observed_at=None):
        """Leaderboard rows {steam_id: {rank, score, details?, name?}} from a Steam read."""
        observed_at = observed_at or self.clock()
        with self.lock:
            for sid, row in (players or {}).items():
                if not STEAM_ID.match(str(sid)) or not isinstance(row, dict):
                    continue
                details = row.get('details') if isinstance(row.get('details'), dict) else None
                self.db.execute('INSERT OR IGNORE INTO ratings VALUES (?,?,?,?,?,?,?,?,?)',
                                (sid, round(observed_at), 'steam', season, row.get('rank'), row.get('score'),
                                 details and details.get('internal'), details and details.get('wins'),
                                 details and details.get('losses')))
                if details:
                    self.db.execute('INSERT OR REPLACE INTO board VALUES (?,?,?,?)',
                                    (sid, season, observed_at, json.dumps(dict(details, rank=row.get('rank'), score=row.get('score')))))
                if row.get('name'):
                    self._name(sid, row['name'], observed_at)
            self.db.commit()

    def record_summary(self, steam_id, summary, observed_at=None):
        """A forts-tools summary (fortstools.summarize_player)."""
        if not summary or not STEAM_ID.match(str(steam_id)):
            return
        observed_at = observed_at or self.clock()
        with self.lock:
            self.db.execute('INSERT OR IGNORE INTO ratings VALUES (?,?,?,?,?,?,?,?,?)',
                            (steam_id, round(observed_at / 600) * 600, 'forts-tools', None, summary.get('rank'),
                             summary.get('score'), summary.get('internal_rating'), summary.get('wins'), summary.get('losses')))
            if summary.get('name'):
                self._name(steam_id, summary['name'], observed_at)
            self.db.commit()

    def latest_internal(self, steam_id):
        with self.lock:
            row = self.db.execute('SELECT internal, observed_at, source FROM ratings WHERE steam_id=? AND internal IS NOT NULL '
                                  'ORDER BY observed_at DESC LIMIT 1', (steam_id,)).fetchone()
        return {'internal': row[0], 'observed_at': row[1], 'source': row[2]} if row else None

    # ----- views -----
    def _rows(self, steam_id):
        with self.lock:
            rows = self.db.execute('SELECT a.key, a.side, a.won, a.ranked, a.mode, a.played, a.metrics, n.map, n.file '
                                   'FROM appearances a JOIN analyses n ON n.key = a.key WHERE a.steam_id=? '
                                   'ORDER BY a.played DESC', (steam_id,)).fetchall()
        return [{'key': r[0], 'side': r[1], 'won': None if r[2] is None else bool(r[2]), 'ranked': bool(r[3]),
                 'mode': r[4], 'played': r[5], 'metrics': json.loads(r[6]), 'map': r[7], 'file': r[8]} for r in rows]

    def norms(self, steam_id, rows=None):
        rows = self._rows(steam_id) if rows is None else rows
        return {cls: baseline([r['metrics'] for r in rows if mode_class(r['mode']) == cls]) for cls in ('duel', 'team')}

    def analysis(self, key, include_player_curves=False):
        with self.lock:
            row = self.db.execute('SELECT data, path FROM analyses WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        legacy_forts = any('starting_team' not in p for p in data['players'])
        legacy_curves = include_player_curves and 'player_curves' not in data
        if legacy_forts or legacy_curves:
            path = Path(row[1])
            try:
                original_exists = self.key(path, path.stat().st_size) == key
            except OSError:
                original_exists = False
            if legacy_forts and original_exists:
                from forts_replay import read_replay
                try:
                    lineup = {p['steam_id']: (p['side'], p['team']) for p in read_replay(path)['players'] if p['steam_id']}
                    for player in data['players']:
                        side, team = lineup.get(player.get('steam_id'), (None, None))
                        if side == player['side']:
                            player['starting_team'] = team
                except (OSError, ValueError):
                    pass
            if legacy_curves and original_exists:
                try:
                    parsed = summarize(self.analyse(path))
                    length = len(data['series_frames'])
                    if parsed['series_frames'][:length] == data['series_frames']:
                        data['player_curves'] = {sid: {metric: values[:length] for metric, values in curves.items()}
                                                  for sid, curves in parsed['player_curves'].items()}
                        with self.lock:
                            self.db.execute('UPDATE analyses SET data=? WHERE key=?',
                                            (json.dumps(data, ensure_ascii=True), key))
                            self.db.commit()
                except (OSError, ValueError, RuntimeError, KeyError, TypeError, TimeoutExpired):
                    logging.info('Could not reload player curves for replay %s.', key, exc_info=True)
        cls = mode_class(data.get('mode'))
        for player in data['players']:
            sid = player.get('steam_id')
            if not sid:
                continue
            norm = self.norms(sid).get(cls, {})
            player['norm'] = norm
            player['deviation'] = compare(player['metrics'], norm)
            player['form'] = form_verdict(player['deviation'])
            player['names'] = self.names(sid)[:3]
        data['key'] = key
        return data

    def names(self, steam_id):
        with self.lock:
            rows = self.db.execute('SELECT name, first_seen, last_seen FROM names WHERE steam_id=? ORDER BY last_seen DESC',
                                   (steam_id,)).fetchall()
        return [{'name': r[0], 'first_seen': r[1], 'last_seen': r[2]} for r in rows]

    def find_key(self, file_name):
        with self.lock:
            row = self.db.execute('SELECT key FROM analyses WHERE file=? ORDER BY played DESC LIMIT 1', (file_name,)).fetchone()
        return row[0] if row else None

    def dossier(self, steam_id, owner=None):
        owner = owner if owner is not None else self.owner()
        rows = self._rows(steam_id)
        norms = self.norms(steam_id, rows)
        with self.lock:
            board = self.db.execute('SELECT season, observed_at, data FROM board WHERE steam_id=?', (steam_id,)).fetchone()
            ratings = self.db.execute('SELECT observed_at, source, season, rank, score, internal, wins, losses FROM ratings '
                                      'WHERE steam_id=? ORDER BY observed_at', (steam_id,)).fetchall()
            together = {}
            if owner and owner != steam_id:
                for key, side in self.db.execute('SELECT key, side FROM appearances WHERE steam_id=?', (owner,)):
                    together[key] = side
        games = []
        record = {'opponent': [0, 0, 0], 'ally': [0, 0, 0]}
        for row in rows:
            cls = mode_class(row['mode'])
            deviation = compare(row['metrics'], norms.get(cls, {}))
            relation = None
            if row['key'] in together:
                relation = 'ally' if together[row['key']] == row['side'] else 'opponent'
                bucket = record[relation]
                bucket[0 if row['won'] else 1 if row['won'] is False else 2] += 1
            games.append(dict(row, deviation=deviation, form=form_verdict(deviation), relation=relation))
        def avg(key, cls):
            values = [g['metrics'][key] for g in games if mode_class(g['mode']) == cls and isinstance(g['metrics'].get(key), (int, float))]
            return round(sum(values) / len(values), 2) if values else None
        decided = [g for g in games if g['won'] is not None]
        details = json.loads(board[2]) if board else None
        if details:
            details.update(season=board[0], observed_at=board[1],
                           opponents=[{'steam_id': sid, 'name': (self.names(sid) or [{}])[0].get('name')}
                                      for sid in details.get('opponents', [])])
        return {
            'steam_id': steam_id, 'names': self.names(steam_id),
            'first_seen': rows[-1]['played'] if rows else None, 'last_seen': rows[0]['played'] if rows else None,
            'games': len(games), 'ranked_games': sum(1 for g in games if g['ranked']),
            'wins': sum(1 for g in decided if g['won']), 'losses': sum(1 for g in decided if not g['won']),
            'with_you': {k: {'wins': v[0], 'losses': v[1], 'unknown': v[2]} for k, v in record.items()},
            'board': details,
            'ratings': [{'at': r[0], 'source': r[1], 'season': r[2], 'rank': r[3], 'score': r[4], 'internal': r[5],
                         'wins': r[6], 'losses': r[7]} for r in ratings][-400:],
            'norms': norms,
            'averages': {cls: {key: avg(key, cls) for key in ('apm', 'cpm', 'dpm', 'damage_ratio', 'floated_metal_pm',
                                                               'floated_energy_pm', 'devices_lost_pm', 'first_weapon')}
                         for cls in ('duel', 'team')},
            'maps': self._maps(games), 'matches': games[:300],
        }

    @staticmethod
    def _maps(games):
        out = {}
        for game in games:
            row = out.setdefault(game['map'] or '—', {'map': game['map'] or '—', 'games': 0, 'wins': 0, 'losses': 0})
            row['games'] += 1
            if game['won'] is True:
                row['wins'] += 1
            elif game['won'] is False:
                row['losses'] += 1
        return sorted(out.values(), key=lambda r: -r['games'])[:12]

    def snapshot(self):
        with self.lock:
            return dict(self.status, revision=str(self.revision))

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    # Recheck priority after each small batch, so a new match
                    # does not wait for the whole older archive to finish.
                    added = self.scan_once(limit=WORKERS)
                except Exception:
                    logging.exception('Replay analysis scan failed')
                    added = 0
                self.stop_event.wait(0.1 if added else SCAN_INTERVAL)
        self.thread = threading.Thread(target=run, daemon=True, name='replay-analysis')
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
        with self.lock:
            self.db.close()
