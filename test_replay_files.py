import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from replay_files import file_played_at_ns, ready_for_analysis


class ReplayFileTests(unittest.TestCase):
    def test_closed_replay_is_ready_after_two_seconds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            stat = path.stat()
            self.assertFalse(ready_for_analysis(path, stat, stat.st_mtime + 1))
            self.assertTrue(ready_for_analysis(path, stat, stat.st_mtime + 3))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing modes')
    def test_paused_writer_is_not_a_finished_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            with path.open('ab') as writer:
                stat = path.stat()
                self.assertFalse(ready_for_analysis(path, stat, stat.st_mtime + 300))
                writer.write(b'next block')
            self.assertTrue(ready_for_analysis(path, path.stat(), path.stat().st_mtime + 3))

    def test_probe_allows_readers_and_releases_its_handle(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            with path.open('rb'):
                stat = path.stat()
                self.assertTrue(ready_for_analysis(path, stat, stat.st_mtime + 3))
            with path.open('ab') as writer:
                writer.write(b'more')

    def test_live_recording_uses_actual_time_despite_a_shifted_filename(self):
        path = Path('Forts v1.39.0_Map_20261110_140000.fwr')
        created = datetime(2026, 11, 10, 18).timestamp()
        ended = created + 20 * 60
        stat = SimpleNamespace(st_ctime=created, st_mtime=ended, st_mtime_ns=int(ended * 1e9))
        self.assertEqual(file_played_at_ns(path, stat), stat.st_mtime_ns)

    def test_copying_an_old_replay_does_not_make_it_the_latest_match(self):
        path = Path('Forts v1.39.0_Map_20261101_140000.fwr')
        copied = datetime(2026, 11, 10, 18).timestamp()
        stat = SimpleNamespace(st_ctime=copied, st_mtime=copied, st_mtime_ns=int(copied * 1e9))
        expected = int(datetime(2026, 11, 1, 14).timestamp() * 1e9)
        self.assertEqual(file_played_at_ns(path, stat), expected)
