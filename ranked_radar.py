"""Background, read-only ranked activity monitor independent of replay capture."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from game_process import forts_running
from ranking_seasons import current_season

POLL_SECONDS = 30
PRESENCE_SECONDS = 60
PRESENCE_BATCH = 12  # profiles per cycle: the roster is covered every few minutes
FRESH_SECONDS = 120
RECENT_SECONDS = 600
TIER_ORDER = {'fpl': 0, 'top20': 1, 'top80': 2, 'current': 3}


def danger_tier(player):
    """One primary tier per account; keep every original qualification."""
    if player.get('fpl'):
        return 'fpl'
    ranks = [s['rank'] for s in player.get('seasons', [])
             if s.get('season') in (40, 41, 42, 43) and type(s.get('rank')) is int]
    if any(1 <= rank <= 20 for rank in ranks):
        return 'top20'
    if any(21 <= rank <= 80 for rank in ranks):
        return 'top80'
    return 'current'


def notification_eligible(player):
    """Only alert for players who reached a top-20 place historically or currently."""
    if player.get('current_top'):
        return True
    return any(s.get('season') in (40, 41, 42, 43) and type(s.get('rank')) is int
               and 1 <= s['rank'] <= 20 for s in player.get('seasons', []))


def native_sample(season, ids):
    from steam_data import _native_lock
    with _native_lock:
        if not forts_running(refresh=True):
            return {'status': 'paused'}
        from steam_service import native
        return native.call('radar', [season, *ids], timeout=30)


class RankedRadar:
    def __init__(self, directory, owner_getter=lambda: ''):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.owner_getter = owner_getter
        self.db = sqlite3.connect(Path(directory) / 'ranked_radar.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS samples (
            season INTEGER, steam_id TEXT, rank INTEGER, score INTEGER, name TEXT,
            was_top INTEGER NOT NULL, observed_at REAL, PRIMARY KEY(season, steam_id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, season INTEGER, steam_id TEXT, before_score INTEGER,
            score INTEGER, before_rank INTEGER, rank INTEGER, detected_at REAL, since REAL)''')
        settings = dict(self.db.execute('SELECT key,value FROM settings'))
        self.enabled = bool(settings.get('enabled', 1))
        # Windows notifications are opt-in (Settings → Notifications).
        self.notify = bool(settings.get('notify', 0))
        self.notifier = None
        seed = json.loads(Path(__file__).with_name('ranked_watchlist.json').read_text(encoding='utf-8'))
        self.roster = {p['steam_id']: p for p in seed['players']}
        self.sources = seed['sources']
        self.roster_updated_at = seed.get('fetched_at')
        self.season = current_season()
        self.top = []
        self._baseline = {}
        self._friends = {}
        self._public = {}
        self._alert_levels = {}
        self._notification = None
        self.checked_at = None
        self.status = 'checking' if self.enabled else 'disabled'
        self.message = ''
        self.threads = []

    def configure(self, enabled, notify):
        if not isinstance(enabled, bool) or not isinstance(notify, bool):
            raise ValueError('Некорректная настройка радара.')
        with self.lock:
            self.enabled, self.notify = enabled, notify
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                [('enabled', int(enabled)), ('notify', int(notify))])
            self.db.commit()
            self._baseline.clear()
            self._friends.clear()
            self._public.clear()
            self._notification = None
            self._alert_levels.clear()
            self.status = 'checking' if enabled else 'disabled'
        self.wake.set()

    def presence_batch(self):
        """Next slice of the roster, round-robin."""
        ids = self.request_ids()
        if not ids:
            return []
        start = getattr(self, '_presence_cursor', 0) % len(ids)
        batch = [ids[(start + i) % len(ids)] for i in range(min(PRESENCE_BATCH, len(ids)))]
        self._presence_cursor = start + len(batch)
        return batch

    def request_ids(self):
        with self.lock:
            current_ids = [row[0] for row in self.db.execute(
                'SELECT steam_id FROM samples WHERE season=? AND was_top=1 ORDER BY observed_at DESC LIMIT 200',
                (current_season(),))]
            return sorted(set(self.roster) | set(current_ids))

    @staticmethod
    def _valid_row(sid, row):
        return (isinstance(sid, str) and len(sid) == 17 and sid.isascii() and sid.isdigit()
                and isinstance(row, dict) and type(row.get('rank')) is int and row['rank'] > 0
                and type(row.get('score')) is int)

    def ingest_sample(self, value, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.enabled or self.stop.is_set():
                return
            if value.get('status') != 'ok':
                self.status = value.get('status', 'unavailable')
                self.message = value.get('message', '')
                if self.status == 'paused':
                    self._baseline.clear()
                    self._friends.clear()
                return
            season = value.get('season')
            if type(season) is not int or not 1 <= season <= 999:
                self.status = 'unavailable'
                return
            if season != self.season:
                self.season = season
                self._baseline.clear()
                self.top = []
            self._friends = {sid: dict(row, checked_at=now) for sid, row in value.get('presence', {}).items()
                             if sid in set(self.roster) | set(value.get('players', {})) and isinstance(row, dict)}
            self.checked_at = now
            self.status = 'ok' if value.get('leaderboard_status') == 'ok' else 'partial'
            self.message = ''
            if value.get('leaderboard_status') == 'ok':
                players = {sid: row for sid, row in value.get('players', {}).items() if self._valid_row(sid, row)}
                top = [row for row in value.get('top', []) if isinstance(row, dict)
                       and self._valid_row(row.get('steam_id'), row) and row['rank'] <= 20]
                top_ids = {row['steam_id'] for row in top}
                old_top = {p['steam_id'] for p in self.top}
                for row in top:
                    previous = self._baseline.get(row['steam_id'])
                    continuous = previous and 0 < now - previous['observed_at'] <= FRESH_SECONDS
                    row['rank_change'] = previous['rank'] - row['rank'] if continuous else None
                    row['new_entry'] = bool(self._baseline and old_top and row['steam_id'] not in old_top)
                self.top = sorted(top, key=lambda row: row['rank'])
                next_baseline = {}
                for sid, row in players.items():
                    previous = self._baseline.get(sid)
                    if previous and 0 < now - previous['observed_at'] <= FRESH_SECONDS:
                        if row['score'] != previous['score']:
                            self.db.execute('''INSERT INTO events
                                (season,steam_id,before_score,score,before_rank,rank,detected_at,since)
                                VALUES (?,?,?,?,?,?,?,?)''', (season, sid, previous['score'], row['score'],
                                previous['rank'], row['rank'], now, previous['observed_at']))
                    old = self.db.execute('SELECT name,was_top FROM samples WHERE season=? AND steam_id=?',
                                          (season, sid)).fetchone()
                    name = row.get('name') or (old[0] if old else None) or self.roster.get(sid, {}).get('name') or sid
                    was_top = int(sid in top_ids or bool(old and old[1]))
                    self.db.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?)',
                                    (season, sid, row['rank'], row['score'], name, was_top, now))
                    next_baseline[sid] = dict(row, observed_at=now)
                # Missing rows break continuity: never compare over a hole in observations.
                self._baseline = next_baseline
                self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)')
                self.db.commit()
            self._update_notification(now)

    def ingest_profiles(self, profiles, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.enabled or self.stop.is_set():
                return
            # Publish completed profiles immediately: a larger roster must not
            # age early responses while waiting for the entire HTTP batch.
            self._public.update({sid: dict(row, checked_at=row.get('presence_checked_at', now))
                                 for sid, row in profiles.items()})
            self._update_notification(now)

    def _players(self, now):
        current = {row[0]: {'rank': row[1], 'score': row[2], 'name': row[3], 'was_top': bool(row[4]),
                            'rating_checked_at': row[5]} for row in self.db.execute(
            'SELECT steam_id,rank,score,name,was_top,observed_at FROM samples WHERE season=?', (self.season,))}
        ids = set(self.roster) | {sid for sid, row in current.items() if row['was_top']}
        latest = {}
        for event in self.db.execute('''SELECT id,steam_id,before_score,score,before_rank,rank,detected_at,since
                                        FROM events WHERE season=? AND detected_at>=? ORDER BY id DESC''',
                                     (self.season, now - RECENT_SECONDS)):
            latest.setdefault(event[1], {'id': event[0], 'before_score': event[2], 'score': event[3],
                'delta': event[3] - event[2], 'before_rank': event[4], 'rank': event[5],
                'detected_at': event[6], 'since': event[7]})
        rows = []
        for sid in ids:
            seed, rating = self.roster.get(sid, {}), current.get(sid, {})
            public, friend = self._public.get(sid, {}), self._friends.get(sid, {})
            public_fresh = 0 <= now - public.get('checked_at', 0) <= FRESH_SECONDS
            friend_fresh = 0 <= now - friend.get('checked_at', 0) <= FRESH_SECONDS
            playing = bool((public_fresh and public.get('in_forts') is True)
                           or (friend_fresh and friend.get('in_forts') is True))
            queue = bool(friend_fresh and friend.get('in_forts') is True and friend.get('searching_ranked') is True)
            recent = latest.get(sid)
            evidence = 'queue' if queue else 'recent' if recent else 'playing' if playing else 'unknown'
            rows.append({'steam_id': sid, 'name': public.get('name') or rating.get('name') or seed.get('name') or sid,
                         'seasons': seed.get('seasons', []), 'fpl': seed.get('fpl', []), 'tier': danger_tier(seed),
                         'rank': rating.get('rank'), 'score': rating.get('score'),
                         'rating_checked_at': rating.get('rating_checked_at'), 'current_top': any(
                             p['steam_id'] == sid for p in self.top), 'in_forts': playing,
                         'evidence': evidence, 'recent_change': recent})
        order = {'queue': 0, 'recent': 1, 'playing': 2, 'unknown': 3}
        return sorted(rows, key=lambda row: (TIER_ORDER[row['tier']], order[row['evidence']],
                                            row['rank'] or 999999, row['name'].casefold(), row['steam_id']))

    def _update_notification(self, now):
        if not self.notify:
            self._notification = None
            return
        owner = self.owner_getter()
        levels = {'queue': 3, 'recent': 2, 'playing': 1, 'unknown': 0}
        alerts = [p for p in self._players(now) if p['steam_id'] != owner and levels[p['evidence']]
                  and notification_eligible(p)]
        changes = [p for p in alerts if levels[p['evidence']] > self._alert_levels.get(p['steam_id'], 0)]
        self._alert_levels = {p['steam_id']: levels[p['evidence']] for p in alerts}
        if changes:
            # The strongest evidence wins; rows are already ordered by tier.
            first = max(changes, key=lambda p: levels[p['evidence']])
            evidence = first['evidence']
            changes = [p for p in changes if p['evidence'] == evidence and p['tier'] == first['tier']]
            names = ', '.join(p['name'] for p in changes[:3])
            ru = {'queue': 'Подтверждён поиск рейтинга', 'recent': 'Замечено изменение рейтинговых очков',
                  'playing': 'Топ-игроки сейчас в Forts'}[evidence]
            en = {'queue': 'Ranked search confirmed', 'recent': 'Ranked score changes detected',
                  'playing': 'Top players are in Forts'}[evidence]
            tier_ru = {'fpl': 'FPL · очень высокая опасность', 'top20': 'Топ-20 · высокая опасность',
                       'top80': 'Топ-80 · повышенная опасность', 'current': 'Топ текущего сезона'}[first['tier']]
            tier_en = {'fpl': 'FPL · very high danger', 'top20': 'Top 20 · high danger',
                       'top80': 'Top 80 · elevated danger', 'current': 'Current-season top'}[first['tier']]
            key = hashlib.sha256(f'{now}|{names}|{evidence}'.encode()).hexdigest()[:20]
            self._notification = {'id': key, 'created_at': now, 'title_ru': 'Осторожно с рейтингом',
                                  'title_en': 'Ranked activity warning', 'body_ru': f'{ru}: {names}.',
                                  'body_en': f'{tier_en}. {en}: {names}.', 'tier': first['tier'],
                                  'evidence': evidence, 'names': [p['name'] for p in changes],
                                  'steam_ids': [p['steam_id'] for p in changes]}
            self._notification['body_ru'] = f'{tier_ru}. {ru}: {names}.'
            if self.notifier:
                self.notifier.radar_alert(self._notification)

    def snapshot(self, owner_id='', now=None):
        now = time.time() if now is None else now
        running = forts_running()
        with self.lock:
            status = self.status if self.enabled and running else 'paused' if self.enabled else 'disabled'
            if not running:
                self._baseline.clear()
            players = self._players(now)
            alerts = [p for p in players if p['steam_id'] != owner_id and p['evidence'] != 'unknown']
            level = ('queue' if any(p['evidence'] == 'queue' for p in alerts) else
                     'recent' if any(p['evidence'] == 'recent' for p in alerts) else
                     'playing' if alerts else 'unknown') if status not in ('paused', 'disabled') else 'unknown'
            danger = min((p['tier'] for p in alerts), key=TIER_ORDER.get) if alerts and level != 'unknown' else None
            tiers = [{'id': tier, 'count': sum(p['tier'] == tier for p in players),
                      'active_count': sum(p['tier'] == tier for p in alerts) if level != 'unknown' else 0}
                     for tier in TIER_ORDER]
            events = []
            for row in self.db.execute('''SELECT id,steam_id,before_score,score,before_rank,rank,detected_at,since
                                          FROM events WHERE season=? ORDER BY id DESC LIMIT 40''', (self.season,)):
                events.append({'id': row[0], 'steam_id': row[1], 'before_score': row[2], 'score': row[3],
                               'delta': row[3] - row[2], 'before_rank': row[4], 'rank': row[5],
                               'detected_at': row[6], 'since': row[7]})
            notification = self._notification if (self.notify and running and self.enabled
                and self._notification and 0 <= now - self._notification['created_at'] <= FRESH_SECONDS
                and any(sid != owner_id and notification_eligible(
                    next((p for p in players if p['steam_id'] == sid), {}))
                    for sid in self._notification['steam_ids'])) else None
            return {'enabled': self.enabled, 'notify': self.notify, 'status': status, 'season': self.season,
                    'checked_at': self.checked_at, 'interval': POLL_SECONDS, 'recent_seconds': RECENT_SECONDS,
                    'historical_count': len(self.roster), 'seasons': [40, 41, 42, 43], 'level': level,
                    'danger': danger, 'tiers': tiers, 'roster_updated_at': self.roster_updated_at,
                    'players': players, 'top': self.top, 'events': events, 'notification': notification,
                    'sources': self.sources, 'message': self.message}

    def refresh(self):
        self.wake.set()

    def start(self):
        def monitor():
            due = 0
            while not self.stop.is_set():
                forced = self.wake.wait(1)
                self.wake.clear()
                if self.stop.is_set():
                    break
                if not self.enabled or not forts_running():
                    with self.lock:
                        self.status = 'paused' if self.enabled else 'disabled'
                        self._baseline.clear()
                        self._friends.clear()
                        self._public.clear()
                        self._notification = None
                        self._alert_levels.clear()
                    due = 0
                    continue
                if forced or time.monotonic() >= due:
                    try:
                        self.ingest_sample(native_sample(current_season(), self.request_ids()))
                    except Exception:
                        with self.lock:
                            self.status = 'unavailable'
                            self.message = 'Не удалось проверить рейтинг Steam.'
                    due = time.monotonic() + POLL_SECONDS

        def public_presence():
            due = 0
            while not self.stop.wait(1):
                if not self.enabled or not forts_running():
                    due = 0
                    continue
                if time.monotonic() < due:
                    continue
                from steam_data import profile
                # HTTP presence refresh cannot delay the score polling thread, and it
                # walks the roster in small batches: Steam throttles bursts of profile requests.
                ids = self.presence_batch()
                generation = self.season
                for sid in ids:
                    if self.stop.is_set() or not self.enabled or not forts_running():
                        break
                    value = profile(sid, force=True)
                    if value and value.get('throttled'):
                        break
                    if value and forts_running() and self.enabled and generation == self.season:
                        self.ingest_profiles({sid: value})
                if self.stop.is_set():
                    break
                due = time.monotonic() + PRESENCE_SECONDS

        self.threads = [threading.Thread(target=monitor, name='ranked-radar', daemon=True),
                        threading.Thread(target=public_presence, name='ranked-presence', daemon=True)]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=35)
        with self.lock:
            self.db.close()
