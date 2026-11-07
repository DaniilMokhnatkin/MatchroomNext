import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from player_db import PlayerDB, mode_of, parse_details
from replay_analysis import baseline, compare, form_verdict, summarize

A, B, C = '76561197960265901', '76561197960265906', '76561197960265904'


def fwr_info(winner=2, apm_a=(100, 140), apm_b=(150, 130), dmg_b=12000.0):
    """A minimal fwr info --json document for a 1v1 of 120 seconds."""
    return {
        'duration': 120.0, 'ranked': True, 'map': 'maps/Hillfort/Hillfort.fwe', 'parser_version': 'v0.6.0',
        'players': [{'id': 1, 'slot': 0, 'team': 101, 'side': 1, 'steam_id': A, 'name': 'Me', 'recorder': True},
                    {'id': 2, 'slot': 1, 'team': 102, 'side': 2, 'steam_id': B}],
        'commanders': [{'side': 1, 'name': 'commander-ee-marksman'}, {'side': 2, 'name': 'commander-cf-phantom'}],
        'result': {'winner': winner, 'frame': 3000, 'source': 'ranked', 'rating': 1467},
        'timelines': [{'player': 1, 'counts': {'fire': 10, 'repair_area': 6, 'strut': 40}},
                      {'player': 2, 'counts': {'fire': 30, 'ability': 2, 'link': 50}}],
        'state': {
            'frames': [0, 1500, 3000],
            'teams': [
                {'team': 101, 'side': 1, 'metal': [0, 5, 9], 'energy': [1, 2, 3], 'max_metal': [1200] * 3, 'max_energy': [7000] * 3,
                 'floated_metal': [0, 50, 120], 'floated_energy': [0, 900, 2000], 'damage_dealt': [0, 300, 600],
                 'reactor_health': [100, 60, 0], 'struts': [10] * 3, 'strut_hp': [1] * 3, 'struts_lost': [0, 4, 6],
                 'nodes': [5] * 3, 'devices': [10, 12, 4], 'weapons': [3, 4, 1]},
                {'team': 102, 'side': 2, 'metal': [0, 5, 9], 'energy': [1, 2, 3], 'max_metal': [1200] * 3, 'max_energy': [7000] * 3,
                 'floated_metal': [0, 10, 20], 'floated_energy': [0, 100, 200], 'damage_dealt': [0, 5000, dmg_b],
                 'reactor_health': [100, 100, 100], 'struts': [10] * 3, 'strut_hp': [1] * 3, 'struts_lost': [0, 0, 1],
                 'nodes': [5] * 3, 'devices': [10, 14, 16], 'weapons': [3, 5, 7]}],
            'sides': [{'side': 1, 'damage_taken': [0, 5000, dmg_b]}, {'side': 2, 'damage_taken': [0, 300, 600]}],
            'players': [{'slot': 0, 'team': [101, 101, 101], 'damage': [0, 300, 600], 'apm': list(apm_a)},
                        {'slot': 1, 'team': [102, 102, 102], 'damage': [0, 5000, dmg_b], 'apm': list(apm_b)}],
        },
        'losses': [{'frame': 2000, 'team': 101, 'device': 4, 'type_id': 1, 'name': 'sniper', 'weapon': True}],
        'builds': [{'frame': 750, 'team': 102, 'device': 9, 'type_id': 2, 'name': 'machinegun', 'weapon': True},
                   {'frame': 900, 'team': 102, 'device': 10, 'type_id': 3, 'name': 'turbine', 'weapon': False}],
    }


class SummarizeTests(unittest.TestCase):
    def test_per_player_metrics(self):
        summary = summarize(fwr_info(), {B: 'Ironclad'})
        me, him = summary['players']
        self.assertEqual((summary['map'], summary['winner_side'], summary['rating_after']), ('Hillfort', 2, 1467))
        self.assertEqual((me['won'], him['won'], him['name']), (False, True, 'Ironclad'))
        self.assertEqual(me['metrics']['apm'], 120.0)
        self.assertEqual(me['metrics']['dpm'], 300)
        self.assertEqual(him['metrics']['damage_ratio'], 20.0)
        self.assertEqual(me['metrics']['floated_metal_pm'], 60.0)
        self.assertEqual(me['metrics']['devices_lost_pm'], 0.5)
        self.assertEqual(him['metrics']['first_weapon'], 30.0)
        self.assertEqual(him['metrics']['abilities'], 2)
        teams = {t['team']: t for t in summary['teams']}
        self.assertEqual(teams[102]['top_builds'], [('machinegun', 1), ('turbine', 1)])
        self.assertEqual(teams[101]['weapons_lost'], 1)

    def test_baseline_and_verdict(self):
        rows = [{'apm': 100 + i, 'dpm': 2000 + 10 * i, 'devices_lost_pm': 1.0 + i / 100} for i in range(10)]
        norms = baseline(rows)
        self.assertEqual(norms['apm']['n'], 10)
        off = compare({'apm': 60, 'dpm': 900, 'devices_lost_pm': 3.0}, norms)
        self.assertEqual({k: v['verdict'] for k, v in off.items()}, {'apm': 'below', 'dpm': 'below', 'devices_lost_pm': 'below'})
        self.assertEqual(form_verdict(off), 'rough')
        peak = compare({'apm': 140, 'dpm': 3000, 'devices_lost_pm': 0.2}, norms)
        self.assertEqual(form_verdict(peak), 'life')
        self.assertEqual(compare({'apm': 1}, baseline(rows[:4])), {})  # fewer than five games: no verdict

    def test_five_form_levels_and_insufficient_data(self):
        def verdict(*rows):
            return form_verdict(dict(enumerate({'verdict': label, 'z': z} for label, z in rows)))

        plus, minus, neutral = ('above', 1.5), ('below', -1.5), ('usual', 0.4)
        self.assertEqual(verdict(plus, plus, neutral), 'above')
        self.assertEqual(verdict(minus, minus, neutral), 'below')
        self.assertEqual(verdict(plus, minus, neutral), 'usual')
        self.assertEqual(verdict(plus, plus), None)
        self.assertEqual(verdict(plus, plus, plus), 'above')

    def test_starting_fort_is_not_the_last_fort_the_player_controlled(self):
        info = fwr_info()
        info['state']['teams'].append(dict(info['state']['teams'][0], team=201))
        info['state']['players'][0]['team'] = [101, 201, 201]
        me = summarize(info)['players'][0]
        self.assertEqual(me['starting_team'], 101)
        self.assertEqual(me['team'], 201)

    def test_player_damage_is_retained_when_a_player_leaves(self):
        info = fwr_info()
        info['state']['players'][0]['damage'] = [0, 900, 0]
        summary = summarize(info)
        self.assertEqual(summary['player_curves'][A]['damage'], [0, 900, 900])
        self.assertEqual(summary['player_curves'][B]['apm'], [150, 130])


class DetailsTests(unittest.TestCase):
    def test_parse_board_details(self):
        values = [4466333, 1, 1790509993, 1673, 112, 19, 0, 0]
        for sid in (int(B), int(C)):
            values += list(struct.unpack('<ii', struct.pack('<Q', sid)))
        parsed = parse_details(values)
        self.assertEqual((parsed['internal'], parsed['wins'], parsed['losses']), (1673, 112, 19))
        self.assertEqual(parsed['opponents'], [B, C])
        self.assertIsNone(parse_details([1, 2, 3]))
        self.assertIsNone(parse_details([0, 1, 0, 99999, 1, 1]))


class PlayerDBTests(unittest.TestCase):
    def test_old_analysis_recovers_starting_forts_from_the_replay_header(self):
        summary = summarize(fwr_info())
        summary['teams'].append(dict(summary['teams'][0], team=201))
        for player in summary['players']:
            player.pop('starting_team', None)
        summary['players'][0]['team'] = 201
        header = {'players': [{'steam_id': A, 'team': 101, 'side': 1}, {'steam_id': B, 'team': 102, 'side': 2}]}
        with tempfile.TemporaryDirectory() as folder:
            db = PlayerDB(folder, database=':memory:')
            self.addCleanup(db.close)
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            key = db.key(path, 64)
            db.store_summary(key, path.name, str(path), 1, summary)
            with patch('forts_replay.read_replay', return_value=header):
                self.assertEqual(db.analysis(key)['players'][0]['starting_team'], 101)
            path.unlink()
            self.assertIsNone(db.analysis(key)['players'][0].get('starting_team'))

    def test_player_curves_for_archived_matches_are_loaded_only_on_demand_and_saved(self):
        info = fwr_info()
        summary = summarize(info)
        expected = summary.pop('player_curves')
        with tempfile.TemporaryDirectory() as folder:
            analyser = unittest.mock.Mock(return_value=info)
            db = PlayerDB(folder, analyse=analyser, database=':memory:')
            self.addCleanup(db.close)
            path = Path(folder) / 'match.fwr'
            path.write_bytes(b'x' * 64)
            key = db.key(path, 64)
            db.store_summary(key, path.name, str(path), 1, summary)
            self.assertNotIn('player_curves', db.analysis(key))
            analyser.assert_not_called()
            self.assertEqual(db.analysis(key, include_player_curves=True)['player_curves'], expected)
            path.unlink()
            self.assertEqual(db.analysis(key, include_player_curves=True)['player_curves'], expected)
            analyser.assert_called_once_with(path)

    def test_store_dossier_and_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            db = PlayerDB(folder, owner=lambda: A, analyse=lambda path: fwr_info())
            for day in range(6):
                path = Path(folder) / f'44_Forts v1.39.0_Hillfort_202611{day + 1:02d}_120000_0_r1.fwr'
                path.write_bytes(b'x' * 64)
                # Header names come from our own reader; garbage files simply keep fwr names.
                db.add_analysis(PlayerDB.key(path, 64), path)
            db.record_board(44, {B: {'rank': 5, 'score': 4600, 'details': {'internal': 1560, 'wins': 9, 'losses': 2,
                                                                         'last_match': 1790000000, 'opponents': [A]}}})
            dossier = db.dossier(B)
            self.assertEqual(dossier['games'], 6)
            self.assertEqual(dossier['with_you']['opponent'], {'wins': 6, 'losses': 0, 'unknown': 0})
            self.assertEqual(dossier['board']['internal'], 1560)
            self.assertEqual(dossier['board']['opponents'][0]['name'], 'Me')
            self.assertEqual(db.latest_internal(B)['internal'], 1560)
            self.assertEqual(db.latest_internal(A)['source'], 'replay')
            self.assertEqual(dossier['averages']['duel']['apm'], 140.0)
            key = dossier['matches'][0]['key']
            analysis = db.analysis(key)
            self.assertEqual(analysis['players'][1]['form'], 'usual')
            self.assertEqual(db.find_key(dossier['matches'][0]['file']), key)
            db.close()

    def test_solo_games_are_skipped(self):
        info = fwr_info()
        info['players'][1].pop('steam_id')
        with tempfile.TemporaryDirectory() as folder:
            db = PlayerDB(folder, analyse=lambda path: info)
            path = Path(folder) / 'Forts v1.39.0_Abyss_20261106_220000.fwr'
            path.write_bytes(b'x' * 64)
            db.add_analysis('k', path)
            self.assertEqual(db.dossier(A)['games'], 0)
            db.close()

    def test_mode_of(self):
        self.assertEqual(mode_of([{'side': 1}, {'side': 2}, {'side': 2}]), '1v2')
        self.assertEqual(mode_of([{'side': 1}]), 'other')


if __name__ == '__main__':
    unittest.main()
