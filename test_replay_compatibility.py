"""Synthetic replay regressions; no personal replay or account data is shipped."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forts_replay import HEADER_END, STEAM_XOR, ReplayError, parse_replay
from match_history import History


def replay_fixture(signature=0x6E, map_path='maps/Desert Ruins/Desert Ruins.fwe'):
    data = bytearray(128)
    data[0] = signature
    struct.pack_into('<I', data, 1, 5)
    encoded_path = map_path.encode('utf-8')
    data += struct.pack('<I', len(encoded_path)) + encoded_path
    data += struct.pack('<iii', 1, 101, 2)
    for slot, name in enumerate(('Player One', 'Player Two')):
        record = bytearray(101)
        struct.pack_into('<iii', record, 0, slot, slot + 1, 101 + slot)
        encoded_name = name.encode('utf-16-le')
        record[12:12 + len(encoded_name)] = encoded_name
        steam_id = 0x0110000100000001 + slot
        low, high = 23 + slot, 41 + slot
        struct.pack_into('<IIQ', record, 65, low, high,
                         steam_id ^ ((high << 32) | low) ^ STEAM_XOR)
        data += record
    data += b'\xa1' * 4
    struct.pack_into('<I', data, 13, len(data))
    return data + HEADER_END


class ReplayCompatibilityTests(unittest.TestCase):
    def test_both_observed_signatures_keep_players_and_map(self):
        for signature in (0x67, 0x6E):
            with self.subTest(signature=signature):
                parsed = parse_replay(replay_fixture(signature))
                self.assertEqual(parsed['format_version'], 5)
                self.assertEqual(parsed['map'], 'Desert Ruins')
                self.assertEqual([p['role'] for p in parsed['players']], ['self', 'opponent'])
                self.assertEqual([p['steam_id'] for p in parsed['players']],
                                 [str(0x0110000100000001), str(0x0110000100000002)])

    def test_workshop_windows_map_path(self):
        parsed = parse_replay(replay_fixture(map_path='1234567890\\Custom Map.fwe'))
        self.assertEqual(parsed['map'], 'Custom Map')

    def test_unknown_signature_and_serialization_version_remain_rejected(self):
        bad_version = replay_fixture()
        struct.pack_into('<I', bad_version, 1, 6)
        for data in (replay_fixture(0x68), bad_version, replay_fixture()[:-4]):
            with self.subTest(prefix=bytes(data[:5])), self.assertRaises(ReplayError):
                parse_replay(data)

    def test_watcher_imports_new_replay_and_distinguishes_file_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'replays'
            folder.mkdir()
            (folder / '44_Forts v1.39.0_Map_20261027_153100_0_r215.fwr').write_bytes(replay_fixture())
            (folder / '44_bad.fwr').write_bytes(b'not a replay' * 3)
            with patch('game_process.forts_running', return_value=False), \
                    patch('steam_data.activity', return_value={'status': 'paused', 'game_running': False}):
                history = History(root / 'database', [str(folder)])
                try:
                    history.scan_once()
                    history.scan_once()
                    snapshot = history.snapshot()
                    self.assertEqual(snapshot['count'], 1)
                    self.assertEqual(snapshot['status']['folder_errors'], [])
                    self.assertEqual(len(snapshot['status']['replay_errors']), 1)
                    self.assertIn('44_bad.fwr', snapshot['status']['replay_errors'][0])
                    self.assertEqual(history.matches()[0]['map'], 'Desert Ruins')
                finally:
                    history.close()

    def test_missing_folder_is_reported_as_folder_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = History(root / 'database', [str(root / 'missing')])
            try:
                history.scan_once()
                status = history.snapshot()['status']
                self.assertEqual(len(status['folder_errors']), 1)
                self.assertEqual(status['replay_errors'], [])
            finally:
                history.close()


if __name__ == '__main__':
    unittest.main()
