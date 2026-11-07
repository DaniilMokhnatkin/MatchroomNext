"""Optional read-only client for the public forts-tools.com JSON API.

Only public data is requested: the live ranked list and public player pages.
Requests are spaced out, cached and paused after failures so the community
site is never hammered. Nothing local (files, logs, settings) is sent.
"""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = 'https://forts-tools.com'
USER_AGENT = 'MatchroomNext/0.11 (+local desktop app)'
MIN_GAP = 2.0
PLAYER_TTL = 600
LIVE_TTL = 15
MAX_BYTES = 4 * 1024 * 1024
STEAM_ID = re.compile(r'^[0-9]{17}$')


class FortsTools:
    def __init__(self, fetch=None, clock=time.monotonic):
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = {}
        self.last_request = float('-inf')
        self.paused_until = float('-inf')
        self.failures = 0

    @staticmethod
    def _fetch(url):
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
        with urlopen(request, timeout=10) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Response is too large')
        return json.loads(raw)

    def status(self):
        with self.lock:
            wait = max(0.0, self.paused_until - self.clock())
        return {'paused': wait > 0, 'retry_in': round(wait)}

    def _get(self, path, ttl):
        now = self.clock()
        with self.lock:
            cached = self.cache.get(path)
            if cached and now - cached[0] < ttl:
                return cached[1]
            if now < self.paused_until or now - self.last_request < MIN_GAP:
                return cached[1] if cached else None
            self.last_request = now
        try:
            value = self.fetch(BASE + path)
            if not isinstance(value, dict):
                raise ValueError('Unexpected response')
        except (OSError, ValueError, HTTPError):
            with self.lock:
                self.failures += 1
                # 1, 2, 4 ... up to 30 minutes.
                self.paused_until = self.clock() + min(1800, 60 * 2 ** min(self.failures - 1, 5))
                return cached[1] if cached else None
        with self.lock:
            self.failures = 0
            self.cache[path] = (self.clock(), value)
            if len(self.cache) > 300:
                oldest = sorted(self.cache, key=lambda key: self.cache[key][0])[:100]
                for key in oldest:
                    self.cache.pop(key, None)
        return value

    def live(self):
        value = self._get('/api/live', LIVE_TTL)
        return value if value and value.get('schema', '').startswith('forts-live/') else None

    def player(self, steam_id):
        if not isinstance(steam_id, str) or not STEAM_ID.match(steam_id):
            return None
        value = self._get('/api/player/' + steam_id, PLAYER_TTL)
        return value if value and value.get('found') else None

    def live_opponent(self, self_id):
        """Return (opponent_row, match) when a running ranked match lists self_id."""
        data = self.live()
        for match in (data or {}).get('matches', []):
            players = [p for p in match.get('players', []) if isinstance(p, dict)]
            ids = [str(p.get('steam_id') or '') for p in players]
            if self_id in ids and len(players) == 2:
                other = players[1 - ids.index(self_id)]
                if STEAM_ID.match(str(other.get('steam_id') or '')):
                    return other, match
        return None, None


def summarize_player(data, other_id=None):
    """Trim a forts-tools player page to what the live card shows."""
    from efficiency import efficiency
    if not data:
        return None
    season = next((row for row in data.get('season_results') or [] if isinstance(row, dict)), None) or {}
    standing = data.get('standing') if isinstance(data.get('standing'), dict) else {}
    head = next((row for row in data.get('head_to_head') or []
                 if isinstance(row, dict) and other_id and row.get('steam_id') == other_id), None)
    maps = [row for row in data.get('map_winrates') or []
            if isinstance(row, dict) and str(row.get('season')) == str(standing.get('season') or season.get('season'))]
    return {
        'name': data.get('name'), 'rank': data.get('rank'), 'score': data.get('score'),
        'internal_rating': data.get('internal_rating'),
        'wins': data.get('wins'), 'losses': data.get('losses'),
        'career': data.get('career') if isinstance(data.get('career'), dict) else None,
        'top_pct': standing.get('top_pct'), 'field': standing.get('field'),
        'streak': data.get('streak') if isinstance(data.get('streak'), dict) else None,
        'form': [bool(x) for x in (data.get('form_recent') or [])][:10],
        'best_finish': data.get('best_finish') if isinstance(data.get('best_finish'), dict) else None,
        'maps': [{'map': row.get('map'), 'wins': row.get('wins'), 'losses': row.get('losses')} for row in maps][:8],
        'efficiency': efficiency(data.get('history')),
        'head_to_head': {'games': head.get('games'), 'wins': head.get('wins'), 'losses': head.get('losses')} if head else None,
        'last_played': data.get('last_played_utc'),
        'source': data.get('source'),
    }
