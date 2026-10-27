"""In-memory demo of the same Matchroom Next client; no database or Steam calls."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import copy
import shutil
import tempfile
import functools
from pathlib import Path
import threading
import webbrowser
from unittest.mock import patch

from forts_replay import make_server, valid_steam_id
from ranking_seasons import current_season, season_end_date
from windows_notify import WindowsNotifier

SELF = '76561197960265729'
ENEMY = '76561197960265730'
OTHER = '76561197960265731'
ALTERNATE = '76561197960265732'
NAMES = {SELF: 'Northwind', ENEMY: 'Ironclad', OTHER: 'Win', ALTERNATE: 'Frostline'}
# Fictional opponents: (steam id, name, rank, score, chance that Northwind wins).
RIVALS = [
    (ENEMY, 'Ironclad', 18, 4910, .52),
    (OTHER, 'Win', 124, 3120, .78),
    ('76561197960265733', 'Stonebridge', 9, 5040, .42),
    ('76561197960265734', 'Harbor', 61, 4290, .66),
    ('76561197960265735', 'Crosswind', 33, 4580, .58),
    ('76561197960265736', 'Steelcap', 88, 3890, .72),
    ('76561197960265737', 'Aster', 47, 4400, .62),
]
NAMES.update({steam_id: name for steam_id, name, *_ in RIVALS})
MAPS = ['Stalactites 1v1', 'Foundry 1v1', 'High Seas 1v1', 'Canyon Clash 1v1', 'Siege 1v1']
COMMANDERS = ['commander-da-speed-demon', 'commander-da-overclocker', 'commander-ee-marksman',
              'commander-cf-phantom', 'commander-iba-turtle', 'commander-ee-fireman', 'commander-bpo-seep']
DEMO_MATCH_COUNT = 48
CURRENT_SEASON_MATCHES = 38


def owner_rank(score):
    """Fictional score-to-rank curve used by the demo leaderboard."""
    return max(1, round(160 - (score - 3900) / 4.2))


def build_records(season, now=None):
    """Deterministic fictional archive with a consistent rating chain."""
    import random
    rng = random.Random(season * 7919)
    now = now or datetime.now().replace(hour=18, minute=42, second=0, microsecond=0)
    season_start = datetime.combine(season_end_date(season - 1), datetime.min.time()) + timedelta(days=1)
    plan = []
    when = now
    for index in range(CURRENT_SEASON_MATCHES):
        plan.append((when, season))
        when -= timedelta(hours=rng.choice([1, 1, 2, 5, 9, 20, 26]), minutes=rng.randrange(0, 50, 7))
        if when < season_start + timedelta(hours=6):
            when = season_start + timedelta(hours=6 + (CURRENT_SEASON_MATCHES - index) * 3)
    end = datetime.combine(season_end_date(season - 1), datetime.min.time()) + timedelta(hours=20)
    for index in range(DEMO_MATCH_COUNT - CURRENT_SEASON_MATCHES):
        plan.append((end - timedelta(days=index, hours=index % 3), season - 1))

    records = []
    scores = {season: 4140, season - 1: 3990}
    for index, (date, record_season) in reversed(list(enumerate(plan))):
        rival_id, rival_name, rival_rank, rival_score, chance = RIVALS[rng.randrange(len(RIVALS))] if index % 4 else RIVALS[index % 2]
        roll = rng.random()
        result = 'unknown' if index in (6, 29) else 'draw' if index in (13,) else 'win' if roll < chance else 'loss'
        before = scores[record_season]
        delta = 0 if result in ('draw', 'unknown') else rng.randint(17, 31) if result == 'win' else -rng.randint(13, 24)
        confirmed = result != 'unknown'
        after = before + delta
        if confirmed:
            scores[record_season] = after
        own_side = 1 if index % 3 else 2
        rival_side = 2 if own_side == 1 else 1
        record = {
            'match_id': f'demo-{index}', 'file': f'{record_season}_v1_{MAPS[index % len(MAPS)].replace(" ", "_")}_{date:%Y%m%d_%H%M%S}.fwr',
            'map': MAPS[(index * 7 + 3) % len(MAPS)] if index % 5 else MAPS[0],
            'date': date.strftime('%Y%m%d_%H%M%S'), 'played_at_ns': int(date.timestamp() * 1e9),
            'season': record_season, 'recorder_client_id': 1,
            'outcome': result,
            'winner_side': own_side if result == 'win' else rival_side if result == 'loss' else None,
            'players': [
                {'name': NAMES[SELF], 'steam_id': SELF, 'client_id': 1, 'side': own_side, 'team': 100 + own_side, 'role': 'self'},
                {'name': rival_name, 'steam_id': rival_id, 'client_id': 2, 'side': rival_side, 'team': 100 + rival_side, 'role': 'opponent'}],
            'commanders': {str(own_side): COMMANDERS[index % 3], str(rival_side): COMMANDERS[(index * 5 + 1) % len(COMMANDERS)]},
            'warnings': [],
            'historical_ranks': {
                SELF: {'rank': owner_rank(before), 'score': before, 'at_match_start': True, 'age_seconds': 0},
                rival_id: {'rank': rival_rank, 'score': rival_score + rng.randint(-40, 40), 'at_match_start': True, 'age_seconds': 0}},
            'rating_change': {'steam_id': SELF, 'status': 'confirmed' if confirmed else 'pending',
                              'delta': delta if confirmed else None, 'before_score': before,
                              'after_score': after if confirmed else None}}
        if index == 29:
            record['historical_ranks'] = {}
            record['rating_change'].update(status='unavailable', delta=None, before_score=None, after_score=None)
        records.append(record)
    records.reverse()
    return records, scores[season]


class DemoHistory:
    demo = True

    def __init__(self):
        self.lock = threading.RLock()
        self.owner_steam_id = SELF
        self.owner_mode = 'auto'
        self.known_accounts = [SELF, ALTERNATE]
        self.language = 'ru'
        self.revision = 1
        self.empty = False
        self.config = {'enabled': True, 'folders': ['Демопапка / Forts / replays']}
        # The named players, results and ratings are deliberately fictional.
        self.records, self.final_score = build_records(current_season())

    def active_season(self):
        return current_season()

    def snapshot(self, since=None):
        with self.lock:
            state = {'revision': str(self.revision), 'demo': True, 'demo_empty': self.empty,
                     'owner_steam_id': self.owner_steam_id, 'owner_mode': self.owner_mode,
                     'accounts': list(self.known_accounts), 'active_season': current_season(),
                     'season_end': season_end_date(current_season()).isoformat(),
                     'language': self.language, 'watch': copy.deepcopy(self.config),
                     'status': {'scanning': False, 'errors': []}, 'onboarding_complete': True,
                     'owner_last_rating': None}
            if str(since) != str(self.revision):
                state['matches'] = self.matches()
            return state

    def matches(self):
        with self.lock:
            return copy.deepcopy([] if self.empty else self.records)

    def set_empty(self, empty):
        with self.lock:
            self.empty = empty
            self.revision += 1

    def configure(self, enabled, folders):
        if not isinstance(enabled, bool) or not isinstance(folders, list):
            raise ValueError('Некорректные настройки / Invalid settings')
        with self.lock:
            self.config = {'enabled': enabled, 'folders': folders}
            self.revision += 1

    def set_owner_id(self, steam_id):
        if steam_id and (not steam_id.isdigit() or len(steam_id) != 17 or not valid_steam_id(int(steam_id))):
            raise ValueError('Некорректный Steam ID / Invalid Steam ID')
        with self.lock:
            self.owner_steam_id = steam_id or SELF
            self.revision += 1

    def select_account(self, mode, steam_id=''):
        if mode not in ('auto', 'manual'):
            raise ValueError('Invalid account mode')
        if mode == 'manual':
            self.set_owner_id(steam_id)
            if steam_id not in self.known_accounts:
                self.known_accounts.append(steam_id)
        else:
            self.owner_steam_id = SELF
        self.owner_mode = mode
        self.revision += 1

    def set_language(self, value):
        if value not in ('ru', 'en'):
            raise ValueError('Invalid language')
        with self.lock:
            self.language = value
            self.revision += 1

    def set_onboarding_complete(self, complete):
        pass

    def encounters(self, own_id, opponent_id, exclude=None):
        matches = []
        for record in self.matches():
            own = next((p for p in record['players'] if p['steam_id'] == own_id), None)
            other = next((p for p in record['players'] if p['steam_id'] == opponent_id), None)
            if own and other and own['side'] != other['side']:
                item = copy.deepcopy(record)
                item['own_side'] = own['side']
                matches.append(item)
        return {'total': len(matches), 'matches': [m for m in matches if m['match_id'] != exclude]}

    def record_ranking_snapshot(self, *_):
        pass

    def record_leaderboard_gap(self, *_):
        pass

    def add(self, *_):
        raise ValueError('Деморежим не импортирует файлы. Запусти START.cmd / Run START.cmd to import replays.')

    def import_file(self, *_):
        raise ValueError('Demo does not import files')


def demo_profile(steam_id, **_):
    return {'status': 'ok', 'name': NAMES.get(steam_id, 'Demo player'),
            'avatar': '/assets/avatar-self.svg' if steam_id == SELF else '/assets/avatar-opponent.svg'}


@functools.lru_cache(maxsize=4)
def final_score(season):
    return build_records(season)[1]


def demo_enrich(ids, season, leader_id=None, force=False):
    score = final_score(season)
    scores = {SELF: (owner_rank(score), score)}
    scores.update({steam_id: (rank - 1, points + 26) for steam_id, _name, rank, points, _chance in RIVALS})
    return {'profiles': {sid: demo_profile(sid) for sid in ids},
            'leaderboard': {'status': 'ok', 'season': season, 'checked_at': datetime.now(timezone.utc).isoformat(),
                            'players': {sid: {'rank': scores.get(sid, (150, 3000))[0],
                                              'score': scores.get(sid, (150, 3000))[1]} for sid in ids}}}


RADAR_EXTRA = ['Sundown', 'Lighthouse', 'Bastion', 'Quarry', 'Ember', 'Rampart', 'Gale', 'Torrent', 'Keystone',
               'Anvil', 'Vesper', 'Halyard', 'Cinder', 'Parapet', 'Foundling', 'Brightwater', 'Talon']


class DemoRadar:
    def __init__(self):
        self.enabled = True
        self.notify = False
        self.checked_at = datetime.now().timestamp()

    def configure(self, enabled, notify):
        if not isinstance(enabled, bool) or not isinstance(notify, bool):
            raise ValueError('Invalid radar setting')
        self.enabled, self.notify = enabled, notify

    def refresh(self):
        self.checked_at = datetime.now().timestamp()

    def snapshot(self, owner_id=''):
        tiers = ['fpl', 'top20', 'top80']
        rows = [{'steam_id': steam_id, 'name': name, 'tier': 'fpl' if rank <= 20 else 'top80', 'rank': rank - 1,
                 'score': points + 26, 'evidence': 'recent' if steam_id == ENEMY else 'playing' if steam_id == OTHER else 'unknown'}
                for steam_id, name, rank, points, _chance in RIVALS]
        for index, name in enumerate(RADAR_EXTRA):
            rows.append({'steam_id': str(int(SELF) + index + 20), 'name': name, 'tier': tiers[index % 3],
                         'rank': index + 1 + (index > 7), 'score': 5230 - index * 21, 'evidence': 'unknown'})
        return {'status': 'ok' if self.enabled else 'disabled', 'enabled': self.enabled, 'notify': self.notify,
                'season': current_season(), 'checked_at': self.checked_at, 'level': 'recent' if self.enabled else 'unknown',
                'players': rows, 'top': sorted(rows, key=lambda p: p['rank'])[:20],
                'events': [{'steam_id': ENEMY, 'delta': 26, 'before_score': 4910, 'score': 4936,
                            'detected_at': self.checked_at - 180},
                           {'steam_id': OTHER, 'delta': -18, 'before_score': 3159, 'score': 3141,
                            'detected_at': self.checked_at - 420}]}


class DemoLobbies:
    def __init__(self):
        self.mode = 'live'
        self.notify = False
        self.favorites = []
        self.checked_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self):
        now = datetime.fromisoformat(self.checked_at)
        samples = [{'at': (now - timedelta(seconds=300 - i * 60)).isoformat(), 'players': value}
                   for i, value in enumerate([1, 1, 2, 3, 3, 4])]
        rows = []
        names = ['Тренировка 2v2 / Training', 'Northwind’s room', 'Evening artillery', 'Last stand', 'Private practice',
                 'A quiet valley', 'Ranked duel', 'Open fort', 'Комната Win']
        for index, name in enumerate(names):
            players = [4, 2, 3, 4, 1, None, 1, 2, 3][index]
            maximum = [8, 4, 4, 4, 4, None, 2, 6, 4][index]
            points = [max(0, (players or 0) - offset) for offset in [3, 3, 2, 1, 1, 0]]
            room_samples = [{**point, 'players': points[i]} for i, point in enumerate(samples)]
            activity = {'IsNew': index == 0, 'SampleCount': 6, 'Samples': room_samples,
                        'NetChange': points[-1] - points[0], 'CoverageSeconds': 300, 'ObservedSeconds': 420,
                        'UnchangedSeconds': 0, 'Gains': 3, 'Losses': 0}
            if index == 5:
                activity = {'SampleCount': 0, 'Samples': [], 'NetChange': 0, 'CoverageSeconds': 0}
            rows.append({'id': str(109775242275369180 + index), 'name': name,
                         'map': ['Stalactites 2v2', 'Foundry 1v1', 'High Seas 2v2'][index % 3],
                         'host': ['Northwind', 'Ironclad', 'Frostline'][index % 3],
                         'owner_id': SELF if index == 1 else ENEMY,
                         'players': players, 'max_players': maximum,
                         'free_slots': None if maximum is None else maximum - players,
                         'ranked': index == 6, 'in_progress': index == 3,
                         'password': True if index == 4 else None if index == 5 else False,
                         'own': index == 1, 'host_afk': index == 8,
                         'watchable': index == 3, 'activity': activity})
        fresh = self.mode == 'live'
        for row in rows:
            row['can_join'] = fresh and not row['own']
            if not fresh:
                row['activity']['IsNew'] = False
        return {'status': {'live': 'ok', 'manual': 'manual', 'stale': 'stale', 'error': 'error', 'empty': 'ok'}[self.mode],
                'game_running': self.mode != 'manual', 'steam_running': self.mode != 'error',
                'fresh': fresh or self.mode == 'empty', 'manual': self.mode == 'manual', 'checked_at': self.checked_at,
                'busy': False, 'notify': self.notify, 'favorites': list(self.favorites), 'self_id': SELF,
                'lobbies': [] if self.mode == 'empty' else rows, 'notification_sequence': 0, 'new_names': [],
                'message': 'Демо: Steam недоступен / Demo: Steam unavailable' if self.mode == 'error' else ''}

    def scenario(self, mode):
        if mode not in ('live', 'manual', 'stale', 'empty', 'error'):
            raise ValueError('Invalid demo scenario')
        self.mode = mode
        self.checked_at = (datetime.now(timezone.utc) - timedelta(seconds=120 if mode == 'stale' else 0)).isoformat()
        return self.snapshot()

    def refresh(self, once=False):
        return self.scenario('manual' if once else 'live')

    def configure(self, notify=None, favorite=None, forget_last=False):
        from lobby_service import LobbyService
        if notify is not None:
            if not isinstance(notify, bool):
                raise ValueError('Invalid notification setting')
            self.notify = notify
        if favorite is not None:
            if not LobbyService.valid_id(favorite):
                raise ValueError('Invalid lobby ID')
            if favorite in self.favorites:
                self.favorites.remove(favorite)
            else:
                self.favorites.append(favorite)
        return self.snapshot()

    def join(self, lobby_id):
        raise ValueError('Демо не отправляет запросы в Steam / Demo never joins Steam lobbies')

    def sound(self):
        raise ValueError('Демо не воспроизводит системный звук / Demo sound is disabled')


def create_demo_server(port=0, window_mode=False):
    """Return a server and patch stack; caller closes both after serving."""
    history = DemoHistory()
    radar = DemoRadar()
    patches = ExitStack()
    try:
        patches.enter_context(patch('steam_data.enrich', side_effect=demo_enrich))
        patches.enter_context(patch('steam_data.profile', side_effect=demo_profile))
        patches.enter_context(patch('steam_data.activity', side_effect=lambda **_: {
            'status': 'ok', 'game_running': True, 'online_players': 642, 'ranked_waiting_players': 3,
            'checked_at': datetime.now().timestamp()}))
        # Only the explicit check in Settings shows a real toast; demo data raises no alerts.
        notifier = WindowsNotifier(enabled=lambda: radar.notify, language=lambda: history.language)
        server = make_server([], history=history, radar=radar, lobbies=DemoLobbies(), app_version='next-0.4-demo',
                             port=port, window_mode=window_mode, notifier=notifier)
        return server, patches
    except BaseException:
        patches.close()
        raise


def main():
    parser = argparse.ArgumentParser(description='Matchroom Next: isolated demo')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true', help='Do not open any window.')
    parser.add_argument('--browser', action='store_true', help='Open in the default browser instead of an own window.')
    parser.add_argument('--shell', action='store_true', help='Run as the service of the native shell window.')
    parser.add_argument('--parent-pid', type=int)
    args = parser.parse_args()
    import app_window
    window_mode = not args.shell and not args.no_browser and not args.browser and app_window.find_app_browser() is not None
    server, patches = create_demo_server(args.port, window_mode=window_mode)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(('READY ' if args.shell else 'Matchroom Next demo: ') + url, flush=True)
    if args.shell:
        import threading
        from desktop_app import watch_stdin, watch_parent
        threading.Thread(target=watch_stdin, args=(server,), daemon=True).start()
        if args.parent_pid:
            threading.Thread(target=watch_parent, args=(args.parent_pid, server), daemon=True).start()
    # The demo writes nothing permanent: the window profile lives in a temporary folder.
    profile = Path(tempfile.mkdtemp(prefix='matchroom-next-demo-')) if window_mode else None
    window = app_window.launch(url, profile) if window_mode else None
    if window:
        app_window.watch(window, server)
    elif not args.no_browser and not args.shell:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app_window.close(window)
        server.server_close()
        patches.close()
        if profile:
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    main()
