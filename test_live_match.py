import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from efficiency import efficiency, expected, win_chance
from fortstools import FortsTools, summarize_player
from live_match import LiveMatchService, LogFollower, LogParser, TokenBook, head_to_head, resolve

SELF = '76561197960265901'
HOST = '76561197960265900'
JOINER = '76561197960265902'

HOST_LOG = """Logged into Steam as Northwind (76561197960265901)
Entering RankedMatchScreen
Northwind is hosting a game
Creating Host: nameVisibility: 1, enableChat: 1
Please wait for a match
Setting lobby 109775243006731928 game server 90296243196911624
Connected to player
Client Connected: Player, index 1, id 2, side 2, nameVisibility: 0, enableChat 0
Randomly selecting map Hillfort for 1v1 match
AddMapControls dir mods, filter commander-*
All ranked players ready, starting game
4918.559: LaunchGame, show cursors 0, lock teams 1, force start 0, joining 0
client slot 0 id 1: Northwind, team 101, PlayerPlaying (local) (host)
Actual winner 1
Team1 commander: commander-da-overclocker
Team2 commander: commander-ee-fireman
Replay saved as replays/44_Forts v1.39.0_Hillfort_20261031_183345_0_r56.fwr
Quit: ranked match complete
"""


def feed(text, parser=None):
    parser = parser or LogParser()
    for index, line in enumerate(text.splitlines()):
        parser.feed(line, float(index))
    return parser


class ParserTests(unittest.TestCase):
    def test_loading_ping_is_read_once_as_a_loading_measurement(self):
        parser = feed('Entering RankedMatchScreen\n'
                      '0: Northwind, Id 1, Team 2, PlayerLoading, join at 0, (Steam), SteamID 0, , Local 1, ping 0.083\n'
                      '1: Player, Id 2, Team 1, PlayerLoading, join at 0, (Steam), SteamID 0, 0.0.0.0|65535, Local 0, ping 0.099')
        self.assertEqual(parser.match['loading_ping_ms'], {'self': 83, 'opponent': 99})

    def test_winner_finishes_the_match_and_survives_the_next_ranked_screen(self):
        parser = feed(HOST_LOG.split('Actual winner')[0])
        parser.feed('Actual winner 1', 30)
        self.assertEqual(parser.match['phase'], 'finished')
        parser.feed('Team1 commander: commander-da-overclocker', 31)
        parser.feed('Replay saved as replays/44_Forts v1.39.0_Hillfort_20261031_183345_0_r56.fwr', 32)
        parser.feed('Quit: ranked match complete', 33)
        parser.feed('Entering RankedMatchScreen', 34)
        parser.feed('Entering RankedMatchScreen', 35)
        self.assertEqual(parser.match['phase'], 'queue')
        self.assertEqual(parser.last_finished['winner_side'], 1)
        self.assertEqual(parser.last_finished['my_side'], 1)
        self.assertEqual(parser.last_finished['commanders']['1'], 'commander-da-overclocker')
        self.assertTrue(parser.last_finished['replay'].endswith('_r56.fwr'))

    def test_ranked_host_flow(self):
        parser = LogParser()
        lines = HOST_LOG.splitlines()
        for index, line in enumerate(lines[:10]):
            parser.feed(line, float(index))
        match = parser.match
        self.assertEqual(parser.self_id, SELF)
        self.assertEqual((match['phase'], match['ranked'], match['hosting']), ('select', True, True))
        self.assertEqual(match['lobby_id'], '109775243006731928')
        self.assertEqual(match['map'], 'Hillfort')
        for index, line in enumerate(lines[10:], 10):
            parser.feed(line, float(index))
        self.assertEqual(match['phase'], 'finished')
        self.assertEqual((match['my_side'], match['winner_side']), (1, 1))
        self.assertEqual(match['commanders'], {'1': 'commander-da-overclocker', '2': 'commander-ee-fireman'})
        self.assertTrue(match['replay'].startswith('44_'))

    def test_joiner_and_rejected_match(self):
        parser = feed('Entering RankedMatchScreen\nConnecting to lobby\nConnected to player\n'
                      'LoginResult: 0, id 2, index 1, ranked 1, nameVisibility: 0, enableChat: 0\n'
                      'AddMapControls dir mods, filter commander-*')
        self.assertEqual((parser.match['phase'], parser.match['hosting']), ('select', False))
        parser.feed('HandleRankedDisconnect', 9.0)
        self.assertEqual(parser.match['phase'], 'queue')

    def test_casual_lobby_after_ranked_screen(self):
        parser = feed('Entering RankedMatchScreen\nConnecting to lobby\nConnected to 76561197960265903\n'
                      'LoginResult: 0, id 3, index 1, ranked 0, nameVisibility: 1, enableChat: 1\n'
                      'Client Connected: Bob, index 1, id 2, side 2, nameVisibility: 0, enableChat 0')
        self.assertFalse(parser.match['ranked'])
        self.assertEqual(parser.match['phase'], 'lobby')
        self.assertEqual(parser.match['peers'], ['76561197960265903'])
        self.assertEqual(parser.match['roster'], [{'name': 'Bob', 'side': 2}])


class FollowerTests(unittest.TestCase):
    def test_incremental_utf16_and_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log.txt'
            data = '﻿Logged into Steam as Ж (76561197960265901)\r\nEntering Ranked'.encode('utf-16-le')
            path.write_bytes(data[:-1])  # cut inside a UTF-16 unit
            follower = LogFollower()
            follower.poll(path, 1.0)
            self.assertEqual(follower.parser.self_id, SELF)
            self.assertEqual(follower.parser.match['phase'], 'idle')
            with path.open('ab') as stream:
                stream.write(data[-1:] + 'MatchScreen\r\n'.encode('utf-16-le'))
            follower.poll(path, 2.0)
            self.assertEqual(follower.parser.match['phase'], 'queue')
            path.write_bytes('﻿hello\r\n'.encode('utf-16-le'))  # the game rewrote the log
            follower.poll(path, 3.0)
            self.assertIsNone(follower.parser.self_id)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.book = TokenBook(Path(self.folder.name) / 'tokens.json')

    def tearDown(self):
        self.folder.cleanup()

    def row(self, owner, tokens, players=2, lobby='1', game_map='Hillfort'):
        return {'id': lobby, 'owner_id': owner, 'ranked': True, 'tokens': tokens, 'players': players,
                'map': game_map, 'rank_score': 4100}

    def test_we_joined_owner_is_opponent_and_self_token_learned(self):
        match = {'lobby_id': '1', 'hosting': False, 'phase': 'select'}
        found = resolve(match, SELF, [self.row(HOST, ['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'])], self.book)
        self.assertEqual((found['steam_id'], found['source'], found['confidence']), (HOST, 'lobby_host', 'confirmed'))
        self.assertEqual(self.book.own(SELF), 'bbbbbbbbbbbbbbbb')

    def test_we_host_token_book_resolves_joiner(self):
        self.book.learn('cccccccccccccccc', JOINER)
        match = {'lobby_id': '7', 'hosting': True, 'phase': 'select'}
        rows = [self.row(SELF, ['dddddddddddddddd', 'cccccccccccccccc'], lobby='7')]
        found = resolve(match, SELF, rows, self.book)
        self.assertEqual(found['steam_id'], JOINER)
        self.assertEqual(self.book.own(SELF), 'dddddddddddddddd')

    def test_unknown_joiner_is_hidden_and_waiting_is_none(self):
        match = {'lobby_id': '7', 'hosting': True, 'phase': 'queue'}
        self.assertIsNone(resolve(match, SELF, [self.row(SELF, ['dddddddddddddddd'], players=1, lobby='7')], self.book))
        found = resolve(match, SELF, [self.row(SELF, ['dddddddddddddddd', 'eeeeeeeeeeeeeeee'], lobby='7')], self.book)
        self.assertIsNone(found['steam_id'])
        self.assertEqual(found['token'], 'eeeeeeeeeeeeeeee')

    def test_known_own_token_finds_lobby_without_id(self):
        self.book.learn('bbbbbbbbbbbbbbbb', SELF, own=True)
        match = {'lobby_id': None, 'hosting': False, 'phase': 'select'}
        rows = [self.row(JOINER, ['1111111111111111', '2222222222222222'], lobby='2'),
                self.row(HOST, ['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'], lobby='3')]
        self.assertEqual(resolve(match, SELF, rows, self.book)['steam_id'], HOST)

    def test_timing_guess_only_when_unique(self):
        match = {'lobby_id': None, 'hosting': False, 'phase': 'select', 'joined_at': 100.0, 'map': 'Hillfort'}
        rows = [self.row(HOST, ['aaaaaaaaaaaaaaaa', 'ffffffffffffffff'], lobby='3'),
                self.row(JOINER, ['1111111111111111', '2222222222222222'], lobby='4', game_map='Split')]
        found = resolve(match, SELF, rows, self.book, {'3': 101.0, '4': 101.0})
        self.assertEqual((found['steam_id'], found['confidence']), (HOST, 'probable'))
        rows[1]['map'] = 'Hillfort'
        self.assertIsNone(resolve(match, SELF, rows, self.book, {'3': 101.0, '4': 101.0}))

    def test_token_book_persists_and_rejects_garbage(self):
        self.assertFalse(self.book.learn('not-a-token', HOST))
        self.assertTrue(self.book.learn('aaaaaaaaaaaaaaaa', HOST))
        self.book.save(force=True)
        again = TokenBook(Path(self.folder.name) / 'tokens.json')
        self.assertEqual(again.get('aaaaaaaaaaaaaaaa'), HOST)


class ServiceTests(unittest.TestCase):
    def test_result_is_captured_when_a_poll_also_reads_the_next_search_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            users = Path(folder) / 'users'
            (users / SELF).mkdir(parents=True)
            log = users / SELF / 'log.txt'
            log.write_text(HOST_LOG + 'Entering RankedMatchScreen\n', encoding='utf-16-le')
            service = LiveMatchService(folder, users_dir=users, running=lambda: True)
            service.tick()
            snapshot = service.snapshot()
            self.assertEqual(snapshot['phase'], 'queue')
            result = snapshot['last_match']
            self.assertTrue(result['result']['won'])
            self.assertEqual(result['map'], 'Hillfort')
            self.assertTrue(result['result']['replay'].endswith('_r56.fwr'))
            service.close()
            again = LiveMatchService(folder, users_dir=Path(folder) / 'absent-users', running=lambda: False)
            again.tick()
            self.assertEqual(again.snapshot()['last_match']['result'], result['result'])
            again.close()

    def test_forts_tools_opponent_is_published_while_steam_is_waiting(self):
        class History:
            def active_season(self):
                return 44

            def encounters(self, *_ids):
                return {'matches': []}

        requested = []
        release_steam = threading.Event()

        def fetch(url):
            sid = url.rsplit('/', 1)[-1]
            requested.append(sid)
            return {'found': True, 'steam_id': sid, 'name': 'Opponent' if sid == HOST else 'Northwind'}

        def rankings(*_args):
            release_steam.wait(3)
            return {'status': 'ok', 'season': 44, 'names': {HOST: 'Steam nickname'},
                    'players': {HOST: {'rank': 2359, 'score': 4027, 'details': {'internal': 945, 'wins': 7, 'losses': 4}}}}

        now = [100.0]
        with tempfile.TemporaryDirectory() as folder:
            tools = FortsTools(fetch=fetch, clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay))
            service = LiveMatchService(folder, history=History(), tools=tools)
            key = (SELF, HOST, True)
            service.intel_key = key
            with patch('steam_data.rankings', side_effect=rankings):
                worker = threading.Thread(target=service._load_intel, args=(key,), daemon=True)
                worker.start()
                try:
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline and not (service.intel or {}).get('opponent'):
                        release_steam.wait(.01)
                    self.assertEqual(service.intel['opponent']['name'], 'Opponent')
                    self.assertTrue(service.intel['loading'])
                finally:
                    release_steam.set()
                    worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(requested, [HOST, SELF])
            self.assertEqual(service.intel['steam_names'][HOST], 'Steam nickname')
            self.assertEqual(service.intel['board'][HOST]['details']['wins'], 7)

    def test_tick_learns_tokens_and_reports_opponent(self):
        class Lobbies:
            def __init__(self):
                self.rows = []
                self.refreshes = 0

            def snapshot(self):
                return {'lobbies': self.rows}

            def refresh(self):
                self.refreshes += 1

        with tempfile.TemporaryDirectory() as folder:
            users = Path(folder) / 'users'
            (users / SELF).mkdir(parents=True)
            log = users / SELF / 'log.txt'
            lines = HOST_LOG.splitlines()
            log.write_bytes(('﻿' + '\r\n'.join(lines[:10]) + '\r\n').encode('utf-16-le'))
            lobbies = Lobbies()
            lobbies.rows = [
                {'id': '9', 'owner_id': JOINER, 'ranked': True, 'tokens': ['cccccccccccccccc'], 'players': 1},
            ]
            clock = [1000.0]
            service = LiveMatchService(folder, lobbies=lobbies, running=lambda: True, users_dir=users,
                                       clock=lambda: clock[0])
            service._load_intel = lambda key: None
            service.tick()
            snapshot = service.snapshot()
            self.assertEqual(snapshot['phase'], 'select')
            self.assertIsNone(snapshot['opponent'])
            self.assertGreaterEqual(lobbies.refreshes, 1)
            lobbies.rows = [{'id': '109775243006731928', 'owner_id': SELF, 'ranked': True,
                             'tokens': ['dddddddddddddddd', 'cccccccccccccccc'], 'players': 2, 'map': 'Hillfort'}]
            clock[0] += 6
            service.tick()
            snapshot = service.snapshot()
            self.assertEqual(snapshot['opponent']['steam_id'], JOINER)
            self.assertEqual(snapshot['opponent']['source'], 'token_book')
            self.assertEqual(snapshot['map'], 'Hillfort')
            service.close()


class EfficiencyTests(unittest.TestCase):
    def test_expected_is_symmetric(self):
        self.assertAlmostEqual(expected(1200, 1200), .5)
        self.assertAlmostEqual(expected(1200, 1300) + expected(1300, 1200), 1)

    def test_efficiency_summary(self):
        rows = [{'won': True, 'rating': 1216, 'points_delta': 16, 'opponent_rating': 1200},
                {'won': False, 'rating': 1184, 'points_delta': -16, 'opponent_rating': 1200},
                {'won': True, 'rating': 1100, 'points_delta': 24, 'opponent_rating': 1300, 'inferred': True},
                {'won': True, 'rating': 1230, 'points_delta': 26, 'opponent_rating': 1350}]
        result = efficiency(rows)
        self.assertEqual((result['games'], result['wins'], result['losses']), (3, 2, 1))
        self.assertEqual(result['upsets'], 1)
        self.assertGreater(result['over_expected'], 0)
        self.assertIsNone(efficiency([{'won': None}]))

    def test_win_chance_prefers_internal(self):
        chance, basis = win_chance({'internal_rating': 1200, 'score': 4000}, {'internal_rating': 1200, 'score': 4400})
        self.assertEqual((round(chance, 2), basis), (.5, 'internal'))
        chance, basis = win_chance({'score': 4000}, {'score': 4400})
        self.assertEqual(basis, 'score')
        self.assertLess(chance, .5)
        self.assertEqual(win_chance({}, {}), (None, None))

    def test_head_to_head(self):
        result = head_to_head({'matches': [{'winner_side': 1, 'own_side': 1, 'date': '20260101_100000'},
                                           {'winner_side': 2, 'own_side': 1, 'date': '20260102_100000'},
                                           {'winner_side': None, 'own_side': 1, 'date': '20260103_100000'}]})
        self.assertEqual((result['games'], result['wins'], result['losses']), (3, 1, 1))
        self.assertIsNone(result['recent'][0]['won'])


class FortsToolsTests(unittest.TestCase):
    def test_two_uncached_profiles_are_fetched_at_least_two_seconds_apart(self):
        now = [100.0]
        calls = []

        def fetch(url):
            calls.append((url.rsplit('/', 1)[-1], now[0]))
            return {'found': True}

        tools = FortsTools(fetch=fetch, clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay))
        self.assertTrue(tools.player(SELF))
        self.assertTrue(tools.player(HOST))
        self.assertEqual(calls, [(SELF, 100), (HOST, 102)])

    def test_live_lookup_cache_and_pause(self):
        calls = []
        now = [0.0]

        def fetch(url):
            calls.append(url)
            if url.endswith('/api/player/' + HOST):
                raise OSError('down')
            return {'schema': 'forts-live/1', 'matches': [{'map': 'Pillars', 'players': [
                {'steam_id': SELF, 'name': 'Me', 'win_chance': .4},
                {'steam_id': HOST, 'name': 'Him', 'win_chance': .6, 'stake': 19}]}]}

        tools = FortsTools(fetch=fetch, clock=lambda: now[0])
        other, match = tools.live_opponent(SELF)
        self.assertEqual((other['steam_id'], match['map']), (HOST, 'Pillars'))
        tools.live_opponent(SELF)
        self.assertEqual(len(calls), 1)  # cached
        now[0] = 100
        self.assertIsNone(tools.player(HOST))
        self.assertTrue(tools.status()['paused'])
        self.assertIsNone(tools.player('not-an-id'))

    def test_summarize_player(self):
        data = {'found': True, 'name': 'BHD', 'rank': 35, 'score': 4353, 'internal_rating': 1271, 'wins': 29, 'losses': 8,
                'standing': {'season': 44, 'top_pct': 4.2}, 'form_recent': [True, False],
                'head_to_head': [{'steam_id': SELF, 'games': 3, 'wins': 2, 'losses': 1}],
                'map_winrates': [{'season': '44', 'map': 'Abyss', 'wins': 5, 'losses': 3}, {'season': '43', 'map': 'Old'}],
                'history': [{'won': True, 'rating': 1271, 'points_delta': 16, 'opponent_rating': 1182}]}
        summary = summarize_player(data, SELF)
        self.assertEqual(summary['head_to_head'], {'games': 3, 'wins': 2, 'losses': 1})
        self.assertEqual([row['map'] for row in summary['maps']], ['Abyss'])
        self.assertEqual(summary['efficiency']['games'], 1)
        self.assertIsNone(summarize_player(None))


if __name__ == '__main__':
    unittest.main()
