"""Per-replay, per-player match analysis built into Next.

The decoding is done by the bundled fwr parser (github.com/an0nfunc/forts-replay,
AGPL-3.0, run unchanged as a separate executable). This module only runs it and
condenses its JSON into what Next stores and shows: per player APM, damage,
economy, losses and builds, and the team series for charts.
"""
from __future__ import annotations

import json
from itertools import accumulate
import subprocess
import sys
from pathlib import Path

FWR_TIMEOUT = 90
MAX_SERIES = 120
REPAIR = ('repair_area', 'repair_device', 'repair_link')
FIRE = ('fire', 'fire_at')
BUILD = ('strut', 'link', 'place', 'upgrade')


def fwr_path() -> Path | None:
    roots = [Path(__file__).resolve().parent]
    if getattr(sys, 'frozen', False):
        roots.insert(0, Path(getattr(sys, '_MEIPASS', '')))
    for root in roots:
        candidate = root / 'fwr' / 'fwr.exe'
        if candidate.is_file():
            return candidate
    return None


def run_fwr(path: Path, executable: Path | None = None, timeout=FWR_TIMEOUT) -> dict:
    executable = executable or fwr_path()
    if not executable:
        raise RuntimeError('Анализатор реплеев не найден.')
    result = subprocess.run(
        [str(executable), 'info', '--json', '--max-commands', '0', '--max-losses', '400', '--max-builds', '600', str(path)],
        capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode not in (0,) or not result.stdout:
        raise ValueError((result.stderr or b'').decode('utf-8', 'replace').strip()[:300] or 'fwr failed')
    return json.loads(result.stdout.decode('utf-8'))


def _last(values, default=0):
    return values[-1] if values else default


def _downsample(frames, step):
    samples = frames[::step]
    if frames and (len(frames) - 1) % step:
        samples = [*samples, frames[-1]]
    return [round(value, 1) if isinstance(value, float) else value for value in samples]


def summarize(info: dict, names: dict | None = None) -> dict:
    """Condense fwr info JSON. `names` maps steam_id -> name from our own header reader."""
    names = names or {}
    duration = float(info.get('duration') or 0)
    minutes = max(duration / 60.0, 1 / 60)
    state = info.get('state') or {}
    frames = state.get('frames') or []
    step = max(1, -(-len(frames) // MAX_SERIES))
    result = info.get('result') or {}
    winner = result.get('winner') if result.get('winner') in (1, 2) else None

    teams = {}
    for team in state.get('teams') or []:
        losses = [x for x in info.get('losses') or [] if x.get('team') == team['team']]
        builds = [x for x in info.get('builds') or [] if x.get('team') == team['team']]
        weapon_builds = [x['frame'] for x in builds if x.get('weapon')]
        teams[team['team']] = {
            'team': team['team'], 'side': team.get('side'),
            'damage': round(max(team.get('damage_dealt') or [0])),
            'floated_metal': round(_last(team.get('floated_metal'))),
            'floated_energy': round(_last(team.get('floated_energy'))),
            'struts_lost': int(sum(team.get('struts_lost') or [])),
            'reactor_end': round(_last(team.get('reactor_health'))),
            'devices_peak': max(team.get('devices') or [0]),
            'weapons_peak': max(team.get('weapons') or [0]),
            'devices_lost': len(losses), 'weapons_lost': sum(1 for x in losses if x.get('weapon')),
            'devices_built': len(builds), 'weapons_built': len(weapon_builds),
            'first_weapon': round(min(weapon_builds) / 25.0, 1) if weapon_builds else None,
            'top_builds': _top([x.get('name') for x in builds]),
            'top_losses': _top([x.get('name') for x in losses]),
            'series': {key: _downsample(team.get(key) or [], step) for key in
                       ('metal', 'energy', 'floated_metal', 'floated_energy', 'damage_dealt', 'reactor_health', 'devices', 'weapons')},
        }
    taken = {side.get('side'): round(_last(side.get('damage_taken'))) for side in state.get('sides') or []}
    by_slot = {p.get('slot'): p for p in state.get('players') or []}
    counts = {t.get('player'): t.get('counts') or {} for t in info.get('timelines') or []}

    players = []
    player_curves = {}
    for player in info.get('players') or []:
        side = player.get('side')
        if side not in (1, 2):
            continue
        series = by_slot.get(player.get('slot')) or {}
        apm_values = [v for v in series.get('apm') or [] if v]
        damage = round(max(series.get('damage') or [0]))
        actions = counts.get(player.get('id'), {})
        total_actions = sum(actions.values())
        team_ids = [t for t in series.get('team') or [] if t]
        team = teams.get(team_ids[-1] if team_ids else player.get('team')) or {}
        side_damage = sum(t['damage'] for t in teams.values() if t['side'] == side) or 0
        enemy_taken = taken.get(3 - side) or 0
        own_taken = taken.get(side) or 0
        steam_id = player.get('steam_id')
        start = teams.get(player.get('team'), {})
        starting_team = start.get('team') if start.get('side') == side else None
        if steam_id and series and frames:
            # The replay's player damage counter resets to zero when they leave.
            player_curves[steam_id] = {
                'apm': _downsample(series.get('apm') or [], step),
                'damage': _downsample(list(accumulate(series.get('damage') or [], max)), step),
            }
        players.append({
            'steam_id': steam_id, 'name': player.get('name') or names.get(steam_id) or '',
            'side': side, 'team': team.get('team'), 'starting_team': starting_team, 'recorder': bool(player.get('recorder')),
            'won': (winner == side) if winner else None,
            'metrics': {
                'apm': round(sum(apm_values) / len(apm_values), 1) if apm_values else None,
                'apm_peak': max(apm_values) if apm_values else None,
                'cpm': round(total_actions / minutes, 1),
                'damage': damage, 'dpm': round(damage / minutes),
                'damage_share': round(min(1.0, damage / side_damage), 3) if side_damage else None,
                'damage_ratio': round(enemy_taken / own_taken, 2) if own_taken else None,
                'repairs': sum(actions.get(k, 0) for k in REPAIR),
                'fires': sum(actions.get(k, 0) for k in FIRE),
                'abilities': actions.get('ability', 0),
                'builds': sum(actions.get(k, 0) for k in BUILD),
                'floated_metal_pm': round(team.get('floated_metal', 0) / minutes, 1),
                'floated_energy_pm': round(team.get('floated_energy', 0) / minutes),
                'devices_lost_pm': round(team.get('devices_lost', 0) / minutes, 2),
                'struts_lost_pm': round(team.get('struts_lost', 0) / minutes, 1),
                'first_weapon': team.get('first_weapon'),
            },
            'actions': actions,
        })
    return {
        'duration': round(duration, 1), 'ranked': bool(info.get('ranked')), 'map': _map_name(info.get('map')),
        'winner_side': winner, 'rating_after': result.get('rating') if result.get('source') == 'ranked' else None,
        'commanders': {c.get('side'): c.get('name') for c in info.get('commanders') or []},
        'series_frames': _downsample(frames, step), 'player_curves': player_curves,
        'teams': list(teams.values()), 'damage_taken': taken, 'players': players,
        'parser': info.get('parser_version'), 'warnings': (info.get('warnings') or [])[:5],
        'truncated': bool(info.get('truncated')),
    }


def _top(names, limit=6):
    counts = {}
    for name in names:
        if name:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _map_name(path):
    if not path:
        return ''
    name = str(path).replace('\\', '/').rsplit('/', 1)[-1]
    return name[:-4] if name.lower().endswith('.fwe') else name


# Metrics compared against a player's own norm, with the direction that is "better".
BASELINE = {'apm': 'high', 'cpm': 'high', 'dpm': 'high', 'damage_ratio': 'high',
            'floated_metal_pm': 'low', 'floated_energy_pm': 'low', 'devices_lost_pm': 'low', 'first_weapon': 'low'}


def baseline(rows: list[dict]) -> dict:
    """Mean and spread of each metric over a player's analysed games."""
    out = {}
    for key in BASELINE:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if len(values) < 3:
            continue
        mean = sum(values) / len(values)
        spread = (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** .5
        out[key] = {'mean': round(mean, 2), 'sd': round(spread, 2), 'n': len(values)}
    return out


def compare(metrics: dict, norms: dict) -> dict:
    """Per-metric deviation from the norm: z-score, percent and a verdict."""
    out = {}
    for key, better in BASELINE.items():
        value, norm = metrics.get(key), norms.get(key)
        if not isinstance(value, (int, float)) or not norm or norm['n'] < 5:
            continue
        z = (value - norm['mean']) / norm['sd'] if norm['sd'] > 0 else 0.0
        good = z if better == 'high' else -z
        verdict = 'above' if good >= 1.2 else 'below' if good <= -1.2 else 'usual'
        pct = (value - norm['mean']) / norm['mean'] if norm['mean'] else None
        out[key] = {'z': round(z, 2), 'pct': round(pct, 3) if pct is not None else None, 'verdict': verdict}
    return out


def form_verdict(deviations: dict) -> str | None:
    """Overall read of one game against the player's norm."""
    if len(deviations) < 3:
        return None
    above = [d for d in deviations.values() if d['verdict'] == 'above']
    below = [d for d in deviations.values() if d['verdict'] == 'below']
    balance = len(above) - len(below)
    if balance >= 2:
        return 'life' if balance >= 3 and sum(abs(d['z']) >= 2 for d in above) >= 2 else 'above'
    if balance <= -2:
        return 'rough' if balance <= -3 and sum(abs(d['z']) >= 2 for d in below) >= 2 else 'below'
    return 'usual'
