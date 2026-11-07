import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import steam_data
from steam_service import Gate

PLAYER = '76561198310546409'
OTHER = '76561197960265730'
PROFILE = f'''<profile><steamID64>{PLAYER}</steamID64><steamID>Cached player</steamID>
    <avatarFull>https://avatars.fastly.steamstatic.com/test.jpg</avatarFull>
    <onlineState>in-game</onlineState>
    <inGameInfo><gameLink>https://steamcommunity.com/app/410900</gameLink></inGameInfo></profile>'''.encode()


class SteamProfileTests(unittest.TestCase):
    def setUp(self):
        gate = patch('steam_service.community', Gate(min_gap=0))
        self.gate = gate.start()
        self.addCleanup(gate.stop)
        self.directory = self.enterContext(TemporaryDirectory())
        steam_data._profiles.clear()

    def test_profile_survives_restart_without_reusing_old_presence(self):
        steam_data.configure_profile_cache(self.directory)
        self.addCleanup(steam_data.configure_profile_cache, None)
        with patch('steam_data.urlopen', return_value=io.BytesIO(PROFILE)) as fetch:
            self.assertIs(steam_data.profile(PLAYER)['in_forts'], True)
            fetch.assert_called_once()
        self.assertTrue(Path(self.directory, 'steam-profiles.json').is_file())
        steam_data.configure_profile_cache(None)
        steam_data.configure_profile_cache(self.directory)
        with patch('steam_data.urlopen') as fetch:
            restored = steam_data.profile(PLAYER)
            self.assertEqual(restored['name'], 'Cached player')
            self.assertTrue(restored['avatar'].endswith('/test.jpg'))
            self.assertIsNone(restored.get('in_forts'))
            self.assertIsNone(restored.get('presence_checked_at'))
            fetch.assert_not_called()

    def test_rate_limit_does_not_discard_last_known_identity(self):
        steam_data.configure_profile_cache(self.directory)
        self.addCleanup(steam_data.configure_profile_cache, None)
        with patch('steam_data.urlopen', return_value=io.BytesIO(PROFILE)):
            steam_data.profile(PLAYER)
        steam_data.configure_profile_cache(self.directory)
        refusal = HTTPError('https://steamcommunity.com', 429, 'Too Many Requests',
                            {'Retry-After': '35'}, None)
        with patch('steam_data.urlopen', side_effect=refusal) as fetch:
            self.assertEqual(steam_data.profile(PLAYER, force=True)['name'], 'Cached player')
            self.assertTrue(steam_data.profile(OTHER, force=True)['throttled'])
            fetch.assert_called_once()
        self.assertGreater(self.gate.blocked_for(), 30)

    def test_retry_after_date_is_honoured(self):
        with patch('steam_service.time.time', return_value=0):
            self.assertEqual(self.gate.refused('Thu, 01 Jan 1970 01:00:00 GMT'), 3600)

    def test_already_queued_request_stops_after_refusal(self):
        now = 100.0
        gate = Gate()
        with patch('steam_service.time.monotonic', side_effect=lambda: now):
            self.assertTrue(gate.wait_turn())

            def refuse_during_wait(delay):
                nonlocal now
                gate.refused(30)
                now += delay

            with patch('steam_service.time.sleep', side_effect=refuse_during_wait):
                self.assertFalse(gate.wait_turn())
