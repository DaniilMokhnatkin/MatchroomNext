"""Who you are playing, as early as commander select.

Forts hides the ranked opponent in its own log ("Connected to player"), but it
does log our side of the match: entering the ranked screen, hosting or joining,
our lobby ID, the chosen map, commander select, launch, the winner and the
saved replay. Ranked lobbies are public in Steam's lobby list with the host's
Steam ID (OwnerId) and one stable 16-hex token per seated player (player0 is
the host, player1 the joiner). Combining both:

* we joined  -> the opponent is the lobby owner, exactly;
* we host    -> the opponent is player1; its token is resolved from every
                ranked lobby that player ever hosted while Next was watching,
                from our own replays after the match, or from forts-tools.

Nothing is written to the game, and the log is only read.
"""
from __future__ import annotations

import json
import logging
from queue import Queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

TOKEN = re.compile(r'^[0-9a-f]{16}$')
STEAM_ID = re.compile(r'^[0-9]{17}$')
ACTIVE = ('queue', 'select', 'loading', 'playing')


def known_name(value):
    if isinstance(value, str):
        name = value.strip()
        if name.casefold() not in ('', 'player', 'игрок', '[unknown]') and not STEAM_ID.fullmatch(name):
            return name
    return None


PATTERNS = [
    ('self', re.compile(r'^Logged into Steam as (.*) \(([0-9]{17})\)\s*$')),
    ('ranked_screen', re.compile(r'^Entering RankedMatchScreen')),
    ('hosting', re.compile(r'^(?:.+ is hosting a game|Creating Host:)')),
    ('joining', re.compile(r'^Connecting to lobby')),
    ('lobby', re.compile(r'^Setting lobby ([0-9]{5,20}) game server')),
    ('login', re.compile(r'^LoginResult: 0, id -?[0-9]+, index -?[0-9]+, ranked ([01])')),
    ('peer', re.compile(r'^Connected to ([0-9]{17})\s*$')),
    ('peer_gone', re.compile(r'has disconnected, index [0-9]+, SteamID ([0-9]{17})')),
    ('client', re.compile(r'^Client Connected: (.+?), index [0-9]+, id [0-9]+, side ([0-9]+)')),
    ('map', re.compile(r'^Randomly selecting map (.+) for 1v1 match')),
    ('commander_select', re.compile(r'^(?:Multiplayer\(CommanderSelect|AddMapControls dir mods, filter commander-)')),
    ('loading', re.compile(r'^All ranked players ready, starting game')),
    ('launch', re.compile(r'^[0-9.]+: LaunchGame,')),
    ('slot', re.compile(r'^client slot [0-9]+ id [0-9]+: (.*), team ([0-9]+), \w+ \(local\)')),
    ('winner', re.compile(r'^Actual winner ([0-9]+)')),
    ('commander', re.compile(r'^Team([12]) commander:\s*(commander-[a-z0-9-]+)')),
    ('replay', re.compile(r'^Replay saved as replays/([^/\\]+\.fwr)\s*$')),
    ('ranked_done', re.compile(r'^Quit: ranked match complete')),
    ('rejected', re.compile(r'^(?:HandleRankedDisconnect|rejecting match)')),
    ('leave', re.compile(r'^Leaving last lobby')),
]


def fresh_match(ranked=False, now=0.0):
    return {'phase': 'queue' if ranked else 'idle', 'ranked': ranked, 'hosting': None,
            'lobby_id': None, 'map': None, 'phase_at': now, 'joined_at': None,
            'started_at': None, 'my_side': None, 'winner_side': None, 'commanders': {},
            'replay': None, 'peers': [], 'roster': []}


class LogParser:
    """State machine over Forts log lines; timestamps are the time a line was read."""

    def __init__(self):
        self.self_id = None
        self.self_name = None
        self.match = fresh_match()

    def _phase(self, phase, now):
        if self.match['phase'] != phase:
            self.match['phase'] = phase
            self.match['phase_at'] = now

    def feed(self, line, now):
        line = line.strip()
        if not line:
            return
        for kind, pattern in PATTERNS:
            found = pattern.search(line) if kind == 'peer_gone' else pattern.match(line)
            if found:
                self._apply(kind, found, now)
                return

    def _apply(self, kind, found, now):
        m = self.match
        if kind == 'self':
            self.self_name, self.self_id = found[1], found[2]
        elif kind == 'ranked_screen':
            self.match = fresh_match(True, now)
        elif kind == 'hosting':
            if m['phase'] in ('idle', 'finished'):
                self.match = m = fresh_match(False, now)
                self._phase('lobby', now)
            m['hosting'] = True
        elif kind == 'joining':
            if m['phase'] in ('idle', 'finished'):
                self.match = m = fresh_match(False, now)
                self._phase('lobby', now)
            m['hosting'] = False
            m['joined_at'] = now
            m['lobby_id'] = None
        elif kind == 'lobby':
            m['lobby_id'] = found[1]
        elif kind == 'login':
            if found[1] == '1' and not m['ranked']:
                m['ranked'] = True
                self._phase('queue', now)
            elif found[1] == '0' and m['ranked']:
                # Left the ranked screen and joined an ordinary lobby instead.
                m['ranked'] = False
                self._phase('lobby', now)
        elif kind == 'peer':
            if found[1] not in m['peers']:
                m['peers'].append(found[1])
        elif kind == 'peer_gone':
            if found[1] in m['peers']:
                m['peers'].remove(found[1])
        elif kind == 'client':
            m['roster'] = [r for r in m['roster'] if r['name'] != found[1]][-15:] + [{'name': found[1], 'side': int(found[2])}]
        elif kind == 'map':
            m['map'] = found[1].strip()
            self._phase('select', now)
        elif kind == 'commander_select':
            if m['phase'] in ('queue', 'lobby'):
                self._phase('select', now)
        elif kind == 'loading':
            self._phase('loading', now)
        elif kind == 'launch':
            m['started_at'] = now
            self._phase('playing', now)
        elif kind == 'slot':
            team = int(found[2])
            m['my_side'] = team % 100 if team > 0 else None
        elif kind == 'winner':
            m['winner_side'] = int(found[1])
        elif kind == 'commander':
            m['commanders'][found[1]] = found[2]
        elif kind == 'replay':
            m['replay'] = found[1]
            if m['phase'] in ('playing', 'loading', 'select'):
                self._phase('finished', now)
        elif kind == 'ranked_done':
            self._phase('finished', now)
        elif kind == 'rejected':
            if m['phase'] in ('queue', 'select'):
                m['map'] = None
                self._phase('queue', now)
        elif kind == 'leave':
            if m['phase'] in ('queue', 'lobby', 'select'):
                self._phase('idle', now)


class LogFollower:
    """Read a growing UTF-16 LE log incrementally; restart when the game rewrites it."""

    def __init__(self, parser_factory=LogParser, chunk=4 * 1024 * 1024):
        self.parser_factory = parser_factory
        self.chunk = chunk
        self.path = None
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.parser = parser_factory()

    def reset(self, path=None):
        self.path, self.identity, self.offset, self.pending = path, None, 0, b''
        self.parser = self.parser_factory()

    def poll(self, path, now=None):
        now = time.time() if now is None else now
        path = Path(path)
        if self.path != path:
            self.reset(path)
        try:
            stat = path.stat()
        except OSError:
            return False
        identity = getattr(stat, 'st_ctime_ns', None) or stat.st_ctime
        if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
            self.reset(path)
        self.identity = identity
        if stat.st_size <= self.offset:
            return False
        with path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read(self.chunk)
        self.offset += len(data)
        data = self.pending + data
        if self.offset == len(data) and data.startswith(b'\xff\xfe'):
            data = data[2:]
        usable = len(data) - len(data) % 2
        text = data[:usable].decode('utf-16-le', errors='replace')
        self.pending = data[usable:]
        lines = text.split('\n')
        tail = lines.pop()
        self.pending = tail.encode('utf-16-le') + self.pending
        for line in lines:
            self.parser.feed(line, now)
        return bool(lines)


class TokenBook:
    """Persistent map of ranked lobby player tokens to Steam IDs."""

    LIMIT = 50000

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.tokens, self.selves, self.dirty, self.saved_at = {}, {}, False, 0.0
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            self.tokens = {k: v for k, v in raw.get('tokens', {}).items() if TOKEN.match(k) and STEAM_ID.match(str(v))}
            self.selves = {k: v for k, v in raw.get('self', {}).items() if STEAM_ID.match(k) and TOKEN.match(str(v))}
        except (OSError, ValueError, AttributeError):
            pass

    def learn(self, token, steam_id, own=False):
        if not (isinstance(token, str) and TOKEN.match(token) and isinstance(steam_id, str) and STEAM_ID.match(steam_id)):
            return False
        with self.lock:
            changed = self.tokens.get(token) != steam_id
            if changed:
                self.tokens.pop(token, None)
                self.tokens[token] = steam_id
                while len(self.tokens) > self.LIMIT:
                    self.tokens.pop(next(iter(self.tokens)))
            if own and self.selves.get(steam_id) != token:
                self.selves[steam_id] = token
                changed = True
            self.dirty |= changed
            return changed

    def get(self, token):
        with self.lock:
            return self.tokens.get(token)

    def own(self, steam_id):
        with self.lock:
            return self.selves.get(steam_id)

    def __len__(self):
        return len(self.tokens)

    def save(self, force=False):
        with self.lock:
            if not self.dirty or (not force and time.monotonic() - self.saved_at < 20):
                return
            data = json.dumps({'tokens': self.tokens, 'self': self.selves})
            self.dirty, self.saved_at = False, time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(data, encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            logging.exception('Could not save player tokens')


def resolve(match, self_id, rows, book, full_since=None):
    """Find the opponent of our ranked match in a lobby-list snapshot.

    Returns None while nobody has joined, else a dict with steam_id (None when
    only an unknown token is visible), token, source and confidence.
    """
    full_since = full_since or {}
    ranked = [r for r in rows or [] if r.get('ranked') and isinstance(r.get('tokens'), list)]
    own_token = book.own(self_id) if self_id else None
    row, via = None, None
    if match.get('lobby_id'):
        row = next((r for r in ranked if r.get('id') == match['lobby_id']), None)
        via = 'lobby_id' if row else None
    if row is None and own_token:
        row = next((r for r in ranked if own_token in r['tokens']), None)
        via = 'token' if row else None
    if row is None and self_id and match.get('hosting') is not False:
        row = next((r for r in ranked if r.get('owner_id') == self_id), None)
        via = 'owner' if row else None
    if row is not None:
        tokens = row['tokens']
        if row.get('owner_id') == self_id:
            if tokens:
                book.learn(tokens[0], self_id, own=True)
            if len(tokens) < 2:
                return None
            steam_id = book.get(tokens[1])
            return {'steam_id': steam_id, 'token': tokens[1], 'lobby_id': row.get('id'),
                    'map': row.get('map'), 'rank_score': None,
                    'source': 'token_book' if steam_id else None,
                    'confidence': 'confirmed' if steam_id else None}
        if via == 'lobby_id' and len(tokens) >= 2:
            book.learn(tokens[1], self_id, own=True)
        return {'steam_id': row.get('owner_id') or None, 'token': tokens[0] if tokens else None,
                'lobby_id': row.get('id'), 'map': row.get('map'), 'rank_score': row.get('rank_score'), 'name': known_name(row.get('host')),
                'source': 'lobby_host', 'confidence': 'confirmed'}
    # Joined without knowing our token yet: the one ranked room that filled up
    # right when we connected, on the same map, is almost certainly ours.
    if match.get('hosting') is False and match.get('phase') in ('select', 'loading', 'playing'):
        joined = match.get('joined_at') or 0
        candidates = [r for r in ranked
                      if r.get('owner_id') and r.get('owner_id') != self_id and (r.get('players') or 0) >= 2
                      and (not match.get('map') or r.get('map') == match.get('map'))
                      and full_since.get(r.get('id'), 0) >= joined - 60]
        if len(candidates) == 1:
            row = candidates[0]
            return {'steam_id': row['owner_id'], 'token': row['tokens'][0] if row['tokens'] else None,
                    'lobby_id': row.get('id'), 'map': row.get('map'), 'rank_score': row.get('rank_score'), 'name': known_name(row.get('host')),
                    'source': 'lobby_timing', 'confidence': 'probable'}
    return None


def head_to_head(encounters):
    rows = (encounters or {}).get('matches', [])
    wins = sum(1 for r in rows if r.get('winner_side') and r.get('winner_side') == r.get('own_side'))
    losses = sum(1 for r in rows if r.get('winner_side') and r.get('own_side') and r.get('winner_side') != r.get('own_side'))
    recent = sorted(rows, key=lambda r: r.get('date') or '', reverse=True)[:5]
    return {'games': len(rows), 'wins': wins, 'losses': losses,
            'recent': [{'match_id': r.get('match_id'), 'date': r.get('date'), 'map': r.get('map'),
                        'won': (r.get('winner_side') == r.get('own_side')) if r.get('winner_side') else None}
                       for r in recent]}


class LiveMatchService:
    def __init__(self, directory, history=None, lobbies=None, notifier=None, tools=None,
                 running=None, users_dir=None, clock=time.time):
        self.directory = Path(directory)
        self.history, self.lobbies, self.notifier = history, lobbies, notifier
        self.tools = tools
        self.running = running
        self.users_dir = users_dir
        self.clock = clock
        self.book = TokenBook(self.directory / 'player-tokens.json')
        self.follower = LogFollower()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.settings_path = self.directory / 'live-settings.json'
        self.settings = {'fortstools': True}
        try:
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(saved.get('fortstools'), bool):
                self.settings['fortstools'] = saved['fortstools']
        except (OSError, ValueError, AttributeError):
            pass
        self.match_ref = None
        self.opponent = None
        self.full_since = {}
        self.intel, self.intel_key = None, None
        self.intel_retry_at = 0.0
        self.live_probe = None
        self.live_probe_at = 0.0
        self.last_refresh = 0.0
        self.announced = None
        self.learned_replay = None
        self.game_running = False
        self.players = None

    def configure(self, fortstools=None):
        if fortstools is not None:
            if not isinstance(fortstools, bool):
                raise ValueError('Некорректная настройка forts-tools.')
            with self.lock:
                self.settings['fortstools'] = fortstools
                self.intel_key, self.intel = None, None
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.settings), encoding='utf-8')
            temporary.replace(self.settings_path)
        return self.snapshot()

    def _tools(self):
        return self.tools if self.tools and self.settings['fortstools'] else None

    def _users(self):
        if self.users_dir:
            return Path(self.users_dir)
        try:
            from steam_native import locate_dll
            return locate_dll().parent / 'users'
        except (OSError, RuntimeError):
            return None

    def _log_path(self):
        users = self._users()
        if not users:
            return None
        owner = getattr(self.history, 'owner_steam_id', '') if self.history else ''
        if owner and (users / owner / 'log.txt').is_file():
            return users / owner / 'log.txt'
        try:
            logs = [p for p in users.glob('*/log.txt') if p.is_file()]
        except OSError:
            return None
        return max(logs, key=lambda p: p.stat().st_mtime) if logs else None

    def _rows(self):
        if not self.lobbies:
            return []
        try:
            return self.lobbies.snapshot().get('lobbies', []) or []
        except Exception:
            return []

    def _observe(self, rows, now):
        seen = set()
        for row in rows:
            if not row.get('ranked'):
                continue
            tokens = row.get('tokens') or []
            if tokens and row.get('owner_id'):
                self.book.learn(tokens[0], row['owner_id'])
            if (row.get('players') or 0) >= 2:
                seen.add(row.get('id'))
                self.full_since.setdefault(row.get('id'), now)
        self.full_since = {k: v for k, v in self.full_since.items() if k in seen}

    def tick(self):
        now = self.clock()
        running = bool(self.running() if self.running else True)
        path = self._log_path() if running else None
        if path:
            try:
                self.follower.poll(path, now)
            except OSError:
                logging.exception('Could not read the Forts log')
        parser = self.follower.parser
        match = parser.match
        self_id = parser.self_id or (getattr(self.history, 'owner_steam_id', '') if self.history else '') or ''
        rows = self._rows()
        self._observe(rows, now)
        with self.lock:
            self.game_running = running
            if match is not self.match_ref:
                self.match_ref, self.opponent, self.announced = match, None, None
                self.intel, self.intel_key, self.live_probe_at = None, None, 0.0
            if running and match['ranked'] and match['phase'] in ACTIVE:
                found = resolve(match, self_id, rows, self.book, self.full_since)
                if found and (not self.opponent or not self.opponent.get('steam_id') or
                              (found.get('steam_id') and found['confidence'] == 'confirmed')):
                    old = self.opponent or {}
                    if found.get('steam_id') == old.get('steam_id') and not found.get('name') and old.get('name'):
                        found['name'] = old['name']
                    self.opponent = dict(found, found_at=old.get('found_at') or now)
                if ((not self.opponent or not self.opponent.get('steam_id')) and match['phase'] != 'queue'
                        and self._tools() and self_id and self.live_probe is None and now >= self.live_probe_at):
                    self.live_probe, self.live_probe_at = match, now + 5
                    threading.Thread(target=self._ask_forts_tools, args=(self_id, now, match),
                                     daemon=True, name='live-opponent').start()
                if (not self.opponent or not self.opponent.get('steam_id')) and now - self.last_refresh >= 5 and self.lobbies:
                    self.last_refresh = now
                    try:
                        self.lobbies.refresh()
                    except Exception:
                        pass
            opponent_id = (self.opponent or {}).get('steam_id')
            if opponent_id and self_id:
                key = (self_id, opponent_id, self.settings['fortstools'])
                same = key == self.intel_key
                intel = self.intel if same and self.intel else {}
                retry = (running and match['phase'] in ('select', 'loading', 'playing')
                         and not intel.get('loading') and now >= self.intel_retry_at
                         and (not (intel.get('board') or {}).get(opponent_id)
                              or (self._tools() and not intel.get('opponent'))))
                if not same or retry:
                    self.intel_key, self.intel = key, dict(intel, loading=True)
                    self.intel_retry_at = now + 15
                    threading.Thread(target=self._load_intel, args=(key,), daemon=True, name='live-intel').start()
                if self.announced != opponent_id and match['phase'] in ('select', 'loading'):
                    self.announced = opponent_id
                    self._notify()
        if match['phase'] == 'finished' and match.get('replay') and self.learned_replay != match['replay']:
            self._learn_from_replay(match, self_id)
        self.book.save()

    def _ask_forts_tools(self, self_id, now, match):
        try:
            tools = self._tools()
            if not tools:
                return
            other, live = tools.live_opponent(self_id)
            if not other:
                return
            started = datetime.fromisoformat(live.get('started_utc') or '').timestamp()
            with self.lock:
                previous = self.opponent or {}
                if (self.match_ref is not match or self.follower.parser.match is not match or self._tools() is not tools
                        or match['phase'] not in ('select', 'loading', 'playing') or not self.game_running
                        or previous.get('steam_id') or other['steam_id'] == self_id
                        or not -120 <= started - match['phase_at'] <= 120):
                    return
                own_map, public_map = match.get('map'), live.get('map')
                if own_map and public_map and own_map.casefold().removesuffix(' 1v1') != public_map.casefold().removesuffix(' 1v1'):
                    return
                token = previous.get('token')
                if token:
                    self.book.learn(token, other['steam_id'])
                self.opponent = {'steam_id': other['steam_id'], 'token': token, 'lobby_id': previous.get('lobby_id'),
                                 'map': public_map, 'rank_score': other.get('score'), 'name': known_name(other.get('name')),
                                 'win_chance': other.get('win_chance'), 'stake': other.get('stake'),
                                 'source': 'forts_tools', 'confidence': 'confirmed', 'found_at': now}
        except (TypeError, ValueError):
            pass
        except Exception:
            logging.exception('Could not identify opponent through Forts Tools')
        finally:
            with self.lock:
                if self.live_probe is match:
                    self.live_probe = None

    def _learn_from_replay(self, match, self_id):
        if not self.history:
            return
        try:
            record = next((r for r in self.history.matches() if r.get('file') == match['replay']), None)
        except Exception:
            record = None
        if not record:
            return
        self.learned_replay = match['replay']
        own = next((p for p in record.get('players', []) if p.get('steam_id') == self_id or p.get('role') == 'self'), None)
        other = next((p for p in record.get('players', []) if own and p.get('side') and own.get('side')
                      and p['side'] != own['side'] and p.get('steam_id')), None)
        with self.lock:
            token = (self.opponent or {}).get('token')
            if other and token:
                self.book.learn(token, other['steam_id'])
            if other and (not self.opponent or not self.opponent.get('steam_id')):
                self.opponent = dict(self.opponent or {}, steam_id=other['steam_id'], name=other.get('name'),
                                     source='replay', confidence='confirmed')

    def _load_intel(self, key):
        self_id, opponent_id, use_tools = key
        with self.lock:
            if self.intel_key is not key:
                return
            out = dict(self.intel or {})
        ids = (opponent_id, self_id)
        messages = Queue()
        tools = self.tools if use_tools else None
        missing_summaries = [(sid, other, field)
                             for sid, other, field in ((opponent_id, self_id, 'opponent'), (self_id, opponent_id, 'self'))
                             if not out.get(field)]

        def fetch_board():
            from steam_data import rankings
            season = self.history.active_season() if self.history else None
            if season:
                messages.put(('board', rankings(season, sorted(set(ids)))))

        def fetch_tools():
            from fortstools import summarize_player
            for sid, other, field in missing_summaries:
                messages.put((field, summarize_player(tools.player(sid), other)))

        def start(label, task):
            def run():
                try:
                    task()
                except Exception:
                    logging.exception('Live match data source failed: %s', label)
                finally:
                    messages.put(('done', None))
            threading.Thread(target=run, daemon=True, name='live-' + label).start()

        pending = 0
        if not all((out.get('board') or {}).get(sid) for sid in ids):
            start('steam-board', fetch_board)
            pending += 1
        if tools and missing_summaries:
            start('forts-tools', fetch_tools)
            pending += 1

        names = dict(out.get('names') or {})
        cached_ratings = {}
        try:
            if self.history and 'h2h' not in out:
                encounters = self.history.encounters(self_id, opponent_id)
                out['h2h'] = head_to_head(encounters)
                for encounter in encounters.get('matches', []):
                    if name := known_name(encounter.get('name')):
                        names.setdefault(opponent_id, name)
                        break
            if self.players:
                for sid in ids:
                    for row in self.players.names(sid):
                        if name := known_name(row.get('name')):
                            names.setdefault(sid, name)
                            break
                    cached = self.players.latest_internal(sid)
                    if cached:
                        cached_ratings[sid] = cached['internal']
        except Exception:
            logging.exception('Could not load local live match data')
        out['names'] = names
        from efficiency import win_chance

        def rating(sid, summary):
            row = (out.get('board') or {}).get(sid) or {}
            internal = (row.get('details') or {}).get('internal') or (summary or {}).get('internal_rating') or cached_ratings.get(sid)
            return {'internal_rating': internal, 'score': row.get('score') or (summary or {}).get('score')}

        def publish():
            own = rating(self_id, out.get('self'))
            other = rating(opponent_id, out.get('opponent'))
            chance, basis = win_chance(own, other)
            with self.lock:
                if self.intel_key is key and not self.stop.is_set():
                    self.intel = dict(out, loading=bool(pending), win_chance=chance, chance_basis=basis,
                                      internal={self_id: own['internal_rating'], opponent_id: other['internal_rating']})

        publish()
        while pending:
            field, value = messages.get()
            if field == 'done':
                pending -= 1
            elif field == 'board':
                rows = {sid: row for sid, row in (value.get('players') or {}).items() if sid in ids and row}
                out['board'] = {**(out.get('board') or {}), **rows}
                out['steam_names'] = {**(out.get('steam_names') or {}), **(value.get('names') or {})}
                if self.players and value.get('status') == 'ok' and rows:
                    try:
                        self.players.record_board(value['season'], rows)
                    except Exception:
                        logging.exception('Could not store live Steam standings')
            elif value:
                out[field] = value
                if self.players:
                    try:
                        self.players.record_summary(opponent_id if field == 'opponent' else self_id, value)
                    except Exception:
                        logging.exception('Could not store a forts-tools summary')
            publish()

    def _notify(self):
        method = getattr(self.notifier, 'live_opponent', None)
        if not method:
            return
        opponent = self.opponent or {}
        try:
            method(opponent.get('name') or opponent.get('steam_id'), opponent.get('map'))
        except Exception:
            logging.exception('Live opponent notification failed')

    def snapshot(self):
        with self.lock:
            parser = self.follower.parser
            match = parser.match
            phase = match['phase'] if self.game_running or match['phase'] == 'finished' else 'off'
            opponent = dict(self.opponent) if self.opponent else None
            if opponent:
                opponent.pop('lobby_id', None)
            result = None
            if match['phase'] == 'finished' and match.get('winner_side') and match.get('my_side'):
                result = {'won': match['winner_side'] == match['my_side'], 'winner_side': match['winner_side'],
                          'my_side': match['my_side'], 'replay': match.get('replay'),
                          'commanders': dict(match.get('commanders') or {})}
            tools = self._tools()
            roster = [] if match['ranked'] else (
                [{'steam_id': sid} for sid in match.get('peers', [])] + [dict(r) for r in match.get('roster', [])])
            return {
                'phase': phase, 'ranked': match['ranked'], 'hosting': match['hosting'],
                'map': match.get('map') or (opponent or {}).get('map'),
                'since': match.get('phase_at'), 'started_at': match.get('started_at'),
                'self_id': parser.self_id or (getattr(self.history, 'owner_steam_id', '') if self.history else ''),
                'self_name': parser.self_name,
                'opponent': opponent,
                'intel': dict(self.intel) if self.intel and opponent and opponent.get('steam_id') else None,
                'result': result,
                'roster': roster,
                'tokens_known': len(self.book),
                'fortstools': dict({'enabled': self.settings['fortstools']}, **(tools.status() if tools else {})),
            }

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    logging.exception('Live match tracking failed')
                self.stop.wait(1)
        self.thread = threading.Thread(target=run, daemon=True, name='live-match')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.book.save(force=True)
