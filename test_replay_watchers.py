import json
from contextlib import closing
import os
import sqlite3
import time
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from casual_games import CasualGames
from match_history import History
from player_db import PlayerDB
from replay_analysis import summarize
from test_player_db import fwr_info


class ReplayWatcherTests(unittest.TestCase):
    def test_ranked_history_compares_legacy_and_recorded_times_on_one_scale(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(History(Path(folder), folders=[])) as history:
                older = {'match_id': 'old', 'date': '20261109_200000',
                         'played_at_ns': int(datetime(2026, 11, 9, 20).timestamp() * 1e9)}
                newer = {'match_id': 'new', 'date': '20261110_200000'}
                for game in (older, newer):
                    history.db.execute('INSERT INTO matches VALUES (?,?,?)',
                                       (game['match_id'], game['date'], json.dumps(game)))
                history.db.commit()
                self.assertEqual([g['match_id'] for g in history.matches()], ['new', 'old'])

    def test_casual_batch_commits_when_the_last_replay_changes_during_read(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'match{index}.fwr' for index in range(2)]
            for index, path in enumerate(paths):
                path.write_bytes(b'x' * 64)
                os.utime(path, (time.time() - 300 - index, time.time() - 300 - index))

            def facts(path):
                if path == paths[1]:
                    path.write_bytes(b'x' * 128)
                return {'file': path.name, 'date': '20261110_200000'}

            with closing(CasualGames(Path(folder) / 'data', folders=lambda: [folder])) as db:
                with patch('casual_games.game_facts', side_effect=facts):
                    self.assertEqual(db.scan_once(), 1)
                with closing(sqlite3.connect(db.directory / 'casual.sqlite3')) as reader:
                    self.assertEqual(reader.execute('SELECT COUNT(*) FROM games').fetchone()[0], 1)

    def test_existing_casual_games_get_recording_times_without_reimport(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Forts v1.39.0_Map_20261110_140000.fwr'
            path.write_bytes(b'x' * 64)
            data = Path(folder) / 'data'
            with closing(CasualGames(data, folders=lambda: [folder])) as db:
                game = {'file': path.name, 'date': '20261110_140000'}
                db.db.execute('INSERT INTO games VALUES (?,?,?)', ('old', game['date'], json.dumps(game)))
                db.db.commit()
            expected = int(datetime(2026, 11, 10, 18, 20).timestamp() * 1e9)
            with patch('casual_games.file_played_at_ns', return_value=expected), \
                    closing(CasualGames(data, folders=lambda: [folder])) as db:
                self.assertEqual(db.games()[0]['played_at_ns'], expected)
                self.assertEqual(len(db.games()), 1)

    def test_existing_analyses_and_dossiers_get_correct_times_without_reanalysis(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Forts v1.39.0_Map_20261110_140000.fwr'
            path.write_bytes(b'x' * 64)
            data = Path(folder) / 'data'
            with closing(PlayerDB(data, analyse=lambda _: fwr_info())) as db:
                db.store_summary(db.key(path, 64), path.name, str(path), 1, summarize(fwr_info()))
            expected = int(datetime(2026, 11, 10, 18, 20).timestamp() * 1e9)
            with patch('player_db.file_played_at_ns', return_value=expected), \
                    closing(PlayerDB(data, analyse=lambda _: self.fail('must not reanalyse'))) as db:
                self.assertEqual(db.analysis(db.find_key(path.name))['played'], expected / 1e9)
                self.assertEqual(db._rows(fwr_info()['players'][0]['steam_id'])[0]['played'], expected / 1e9)

    def test_analysis_does_not_wait_ninety_seconds_for_a_closed_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Forts v1.39.0_Map_20261110_200000.fwr'
            path.write_bytes(b'x' * 64)
            with closing(PlayerDB(Path(folder) / 'data', folders=lambda: [folder], analyse=lambda _: fwr_info())) as db:
                self.assertEqual(db.scan_once(now=path.stat().st_mtime + 3), 1)
                self.assertIsNotNone(db.find_key(path.name))

    def test_casual_list_does_not_wait_ninety_seconds_either(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Forts v1.39.0_Map_20261110_200000.fwr'
            path.write_bytes(b'x' * 64)
            with closing(CasualGames(Path(folder) / 'data', folders=lambda: [folder])) as db:
                facts = {'file': path.name, 'date': '20261110_200000'}
                with patch('casual_games.game_facts', return_value=facts):
                    self.assertEqual(db.scan_once(now=path.stat().st_mtime + 3), 1)

    def test_analysis_changed_during_read_is_not_cached_or_marked_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)

            def analyse(source):
                source.write_bytes(b'x' * 128)
                return fwr_info()

            with closing(PlayerDB(Path(folder) / 'data', folders=lambda: [folder], analyse=analyse)) as db:
                self.assertEqual(db.scan_once(now=path.stat().st_mtime + 300), 0)
                self.assertEqual(db.db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 0)
                self.assertEqual(db.db.execute('SELECT COUNT(*) FROM failed').fetchone()[0], 0)

    def test_casual_read_changed_during_read_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            def facts(source):
                source.write_bytes(b'x' * 128)
                return {'file': source.name, 'date': '20261110_200000'}

            with closing(CasualGames(Path(folder) / 'data', folders=lambda: [folder])) as db:
                with patch('casual_games.game_facts', side_effect=facts):
                    self.assertEqual(db.scan_once(now=path.stat().st_mtime + 300), 0)
                self.assertEqual(db.db.execute('SELECT COUNT(*) FROM skipped').fetchone()[0], 0)
                self.assertEqual(db.games(), [])

    def test_cached_casual_rows_sort_by_recording_time_with_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(CasualGames(folder)) as db:
                rows = [
                    {'file': 'legacy.fwr', 'date': '20261110_200000'},
                    {'file': 'shifted.fwr', 'date': '20261110_140000',
                     'played_at_ns': 9999999999999999999},
                ]
                for key, game in enumerate(rows):
                    db.db.execute('INSERT INTO games VALUES (?,?,?)', (str(key), game['date'], json.dumps(game)))
                db.db.commit()
                self.assertEqual([g['file'] for g in db.games()], ['shifted.fwr', 'legacy.fwr'])
