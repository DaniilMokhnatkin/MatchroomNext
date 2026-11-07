"""Fictional replay analyses and dossiers for the demo, kept in memory only."""
from __future__ import annotations

import hashlib
import random
import tempfile
import time
from datetime import datetime

from player_db import PlayerDB

COMMANDERS = ['commander-da-speed-demon', 'commander-da-overclocker', 'commander-ee-marksman',
              'commander-cf-phantom', 'commander-iba-turtle', 'commander-ee-fireman']
DEVICES = ['machinegun', 'sniper', 'turbine', 'battery', 'mine', 'sandbags', 'mortar', 'cannon20mm', 'buzzsaw', 'missile']


def _style(steam_id):
    """Each fictional player keeps a stable playing style."""
    rng = random.Random('style' + steam_id)
    return {'apm': rng.uniform(70, 170), 'dpm': rng.uniform(900, 4200), 'float': rng.uniform(40, 260),
            'loss': rng.uniform(0.4, 2.2), 'weapon': rng.uniform(70, 260)}


def _walk(rng, count, low, high):
    value, out = (low + high) / 2, []
    for _ in range(count):
        value = min(high, max(low, value + rng.uniform(-0.06, 0.06) * (high - low)))
        out.append(round(value))
    return out


def _summary(key, record, self_id):
    rng = random.Random(key)
    duration = rng.uniform(420, 1500)
    minutes = duration / 60
    frames = [int(i * 128) for i in range(int(duration * 25 / 128) + 1)]
    winner = record.get('winner_side')
    players, teams, player_curves = [], [], {}
    # One fort per side in duels; team games share a fort per side for simplicity.
    for side in (1, 2):
        team_id = 100 + side
        won = winner == side if winner else None
        lift = 1.15 if won else 0.88 if won is False else 1.0
        curve = lambda top: [round(top * min(1, (i / max(1, len(frames) - 1)) ** 0.8), 1) for i in range(len(frames))]
        damage = 0
        side_players = [p for p in record['players'] if p.get('side') == side and p.get('steam_id')]
        for player in side_players:
            style = _style(player['steam_id'])
            # Most games sit near the norm; a few are clearly off or clearly sharp.
            mood = rng.choice([1, 1, 1, 1, 1, 0.65, 1.35]) * lift
            apm = style['apm'] * rng.uniform(0.9, 1.1) * (mood ** 0.5)
            dpm = style['dpm'] * rng.uniform(0.85, 1.15) * mood
            dmg = dpm * minutes
            damage += dmg
            player_curves[player['steam_id']] = {'apm': _walk(random.Random(player['steam_id'] + key), len(frames), apm * .5, apm * 1.5),
                                                  'damage': curve(dmg)}
            players.append({
                'steam_id': player['steam_id'], 'name': player.get('name') or '', 'side': side, 'team': team_id,
                'starting_team': team_id,
                'recorder': player['steam_id'] == self_id, 'won': won,
                'metrics': {
                    'apm': round(apm, 1), 'apm_peak': round(apm * 2.1), 'cpm': round(apm * 0.45, 1),
                    'damage': round(dmg), 'dpm': round(dpm), 'damage_share': round(1 / max(1, len(side_players)), 3),
                    'damage_ratio': round(rng.uniform(1.4, 4.5) if won else rng.uniform(0.2, 0.8) if won is False else 1.0, 2),
                    'repairs': rng.randint(10, 140), 'fires': rng.randint(20, 120), 'abilities': rng.randint(0, 9),
                    'builds': rng.randint(300, 900),
                    'floated_metal_pm': round(style['float'] * rng.uniform(0.7, 1.3) / mood, 1),
                    'floated_energy_pm': round(style['float'] * 22 * rng.uniform(0.7, 1.3) / mood),
                    'devices_lost_pm': round(style['loss'] * rng.uniform(0.7, 1.3) / mood, 2),
                    'struts_lost_pm': round(rng.uniform(1, 30), 1),
                    'first_weapon': round(style['weapon'] * rng.uniform(0.8, 1.2) / (mood ** 0.5), 1),
                },
                'actions': {},
            })
        builds = rng.sample(DEVICES, 6)
        teams.append({
            'team': team_id, 'side': side, 'damage': round(damage),
            'floated_metal': round(rng.uniform(100, 3000)), 'floated_energy': round(rng.uniform(5000, 80000)),
            'struts_lost': rng.randint(5, 500), 'reactor_end': 100 if won else 0 if won is False else 60,
            'devices_peak': rng.randint(25, 60), 'weapons_peak': rng.randint(6, 20),
            'devices_lost': rng.randint(5, 70), 'weapons_lost': rng.randint(2, 25),
            'devices_built': rng.randint(30, 90), 'weapons_built': rng.randint(8, 30),
            'first_weapon': round(rng.uniform(60, 300), 1),
            'top_builds': [[name, rng.randint(2, 12)] for name in builds[:4]],
            'top_losses': [[name, rng.randint(1, 9)] for name in builds[2:5]],
            'series': {
                'metal': _walk(rng, len(frames), 300, 1400),
                'energy': _walk(rng, len(frames), 2000, 7000),
                'floated_metal': curve(rng.uniform(100, 3000)),
                'floated_energy': curve(rng.uniform(5000, 80000)),
                'damage_dealt': curve(damage),
                'reactor_health': [100 if won or i < len(frames) * 0.7 else max(0, 100 - (i - len(frames) * 0.7) * 9) for i in range(len(frames))],
                'devices': [min(60, 18 + i // 3) for i in range(len(frames))],
                'weapons': [min(20, 4 + i // 8) for i in range(len(frames))],
            },
        })
    return {
        'duration': round(duration, 1), 'ranked': bool(record.get('ranked')), 'map': record.get('map') or 'Forts',
        'winner_side': winner, 'rating_after': None,
        'commanders': {1: rng.choice(COMMANDERS), 2: rng.choice(COMMANDERS)},
        'series_frames': frames, 'player_curves': player_curves, 'teams': teams, 'damage_taken': {1: 1, 2: 1}, 'players': players,
        'parser': 'demo', 'warnings': [], 'truncated': False,
    }


def _epoch(stamp):
    try:
        return datetime.strptime(stamp, '%Y%m%d_%H%M%S').timestamp()
    except (TypeError, ValueError):
        return time.time()


def build_demo_players(history, casual, self_id):
    db = PlayerDB(tempfile.gettempdir(), database=':memory:', owner=lambda: self_id, enabled=lambda: False)
    records = []
    for match in history.matches():
        records.append({'file': match.get('file') or match['match_id'] + '.fwr', 'date': match.get('date'), 'map': match.get('map'),
                        'ranked': True, 'winner_side': match.get('winner_side'), 'players': match.get('players', [])})
    for game in casual.games:
        records.append({'file': game['file'], 'date': game['date'], 'map': game['map'], 'ranked': False,
                        'winner_side': game.get('winner_side'), 'players': game['players']})
    for record in records:
        key = hashlib.sha1(record['file'].encode('utf-8')).hexdigest()
        db.store_summary(key, record['file'], 'demo', _epoch(record['date']), _summary(key, record, self_id))
    # Steam board rows with hidden details for everyone who played ranked.
    rng = random.Random(9)
    ranked_ids = sorted({p['steam_id'] for r in records if r['ranked'] for p in r['players'] if p.get('steam_id')})
    now = time.time()
    for steam_id in ranked_ids:
        internal = rng.randint(1050, 1450)
        for step in range(12):
            db.record_board(44, {steam_id: {'rank': rng.randint(5, 200), 'score': internal + 3040,
                                            'details': {'internal': internal, 'wins': 10 + step, 'losses': 4 + step // 3,
                                                        'last_match': int(now - 3600 * (12 - step)),
                                                        'opponents': rng.sample([x for x in ranked_ids if x != steam_id], min(6, len(ranked_ids) - 1))}}},
                            observed_at=now - 86400 * (12 - step))
            internal += rng.randint(-18, 22)
    return db
