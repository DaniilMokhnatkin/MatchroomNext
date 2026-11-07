"""Fictional live match and lobby games for the demo; nothing touches Steam or the network."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta

from efficiency import efficiency, win_chance
from live_match import head_to_head

LOBBY_MAPS = ['Ziggurat 4v4', 'Pillars 3v3', 'Mini Bastions 2v2', 'Cliffside 4v4', 'Dueling Vipers 4v4',
              'Stalactites 2v2', 'Heavy Assault 4v4', 'Bombard 2v2']
FRIENDS = ['Lantern', 'Copperhead', 'Driftwood', 'Marlin', 'Saltmarsh', 'Kestrel', 'Bramble', 'Oakheart',
           'Tidewater', 'Ridgeback', 'Sparrow', 'Hollow', 'Pike', 'Juniper']


def demo_history(rng, rating, games=40):
    rows = []
    for _ in range(games):
        opponent = rating + rng.randint(-180, 200)
        chance = 1 / (1 + 10 ** ((opponent - rating) / 400))
        won = rng.random() < min(.92, chance + .08)
        delta = round(32 * (1 - chance)) if won else round(32 * chance)
        rows.append({'won': won, 'rating': rating, 'opponent_rating': opponent,
                     'points_delta': delta if won else -delta, 'inferred': False})
        rating -= delta if won else -delta
    return rows


def demo_summary(name, rank, score, internal, rng, wins, losses, top):
    history = demo_history(rng, internal)
    form = [row['won'] for row in history[:10]]
    current = 0
    for value in form:
        if value != form[0]:
            break
        current += 1
    return {
        'name': name, 'rank': rank, 'score': score, 'internal_rating': internal,
        'wins': wins, 'losses': losses,
        'career': {'games': wins * 6 + losses * 5, 'wins': wins * 6, 'losses': losses * 5,
                   'won_pct': round(100 * wins * 6 / (wins * 6 + losses * 5), 1)},
        'top_pct': top, 'field': 842,
        'streak': {'current': {'n': current, 'won': form[0]}, 'longest_won': 11, 'longest_lost': 4},
        'form': form,
        'best_finish': {'rank': max(1, rank - 6), 'season': 41, 'mode': '1v1'},
        'maps': [{'map': m, 'wins': rng.randint(1, 7), 'losses': rng.randint(0, 4)} for m in
                 ('Stalactites 1v1', 'Foundry 1v1', 'High Seas 1v1', 'Siege 1v1')],
        'efficiency': efficiency(history), 'head_to_head': None, 'last_played': None,
        'source': None,
    }


class DemoLive:
    def __init__(self, history, self_id, enemy_id, enemy_name, enemy_rank, enemy_score, own=(31, 4670)):
        self.history = history
        self.own = own
        self.self_id, self.enemy_id = self_id, enemy_id
        self.enemy = (enemy_name, enemy_rank, enemy_score)
        self.fortstools = True
        self.started = time.time() - 23

    def configure(self, fortstools=None):
        if fortstools is not None:
            if not isinstance(fortstools, bool):
                raise ValueError('Invalid setting')
            self.fortstools = fortstools
        return self.snapshot()

    def snapshot(self):
        rng = random.Random(44)
        name, rank, score = self.enemy
        own_rank, own_score = self.own
        own = demo_summary('Northwind', own_rank, own_score, 1214, rng, 26, 12, 3.7) if self.fortstools else None
        other = demo_summary(name, rank, score, 1288, rng, 31, 9, 1.1) if self.fortstools else None
        h2h = head_to_head(self.history.encounters(self.self_id, self.enemy_id))
        if own and other:
            own['head_to_head'] = {'games': h2h['games'], 'wins': h2h['wins'], 'losses': h2h['losses']}
        chance, basis = win_chance({'internal_rating': (own or {}).get('internal_rating'), 'score': own_score},
                                   {'internal_rating': (other or {}).get('internal_rating'), 'score': score})
        return {
            'phase': 'select', 'ranked': True, 'hosting': True, 'map': 'Foundry 1v1',
            'since': self.started, 'started_at': None, 'self_id': self.self_id,
            'opponent': {'steam_id': self.enemy_id, 'token': '94bab6eb11ee0b43', 'name': name,
                         'source': 'token_book', 'confidence': 'confirmed', 'found_at': self.started + 4},
            'intel': {'loading': False, 'self': own, 'opponent': other, 'h2h': h2h,
                      'board': {self.self_id: {'rank': own_rank, 'score': own_score}, self.enemy_id: {'rank': rank, 'score': score}},
                      'win_chance': chance, 'chance_basis': basis},
            'result': None, 'roster': [], 'tokens_known': 1874,
            'fortstools': {'enabled': self.fortstools, 'paused': False, 'retry_in': 0},
        }


class DemoCasual:
    def __init__(self, self_id):
        rng = random.Random(7)
        now = datetime.now().replace(second=0, microsecond=0)
        games = []
        for index in range(160):
            size = rng.choice([2, 2, 3, 4, 4, 4, 1])
            game_map = rng.choice(LOBBY_MAPS) if size > 1 else rng.choice(['Foundry 1v1', 'Siege 1v1'])
            my_side = rng.choice([1, 2])
            players = [{'name': 'Northwind', 'steam_id': self_id, 'side': my_side, 'role': 'self'}]
            pool = rng.sample(range(len(FRIENDS)), min(len(FRIENDS), size * 2 - 1))
            for slot, friend in enumerate(pool):
                side = my_side if slot < size - 1 else 3 - my_side
                players.append({'name': FRIENDS[friend], 'steam_id': str(76561197960270000 + friend),
                                'side': side, 'role': 'ally' if side == my_side else 'opponent'})
            roll = rng.random()
            result = 'unknown' if roll < .1 else 'win' if roll < .66 else 'loss'
            winner = None if result == 'unknown' else my_side if result == 'win' else 3 - my_side
            now -= timedelta(minutes=rng.choice([25, 40, 70, 300, 1300]))
            games.append({'id': f'demo-casual-{index}', 'file': f'Forts v1.39.0_{game_map}_{now:%Y%m%d_%H%M%S}.fwr',
                          'date': f'{now:%Y%m%d_%H%M%S}', 'map': game_map, 'mode': f'{size}v{size}',
                          'multiplayer': True, 'humans': size * 2, 'players': players, 'self_id': self_id,
                          'my_side': my_side, 'winner_side': winner, 'result': result,
                          'duration': rng.randint(240, 1900) if result != 'unknown' else rng.randint(10, 200)})
        self.games = games

    def snapshot(self, since=None):
        out = {'revision': 'demo-casual', 'status': {'scanning': False, 'pending': 0, 'imported': len(self.games),
                                                     'errors': 0, 'last_scan': time.time()}}
        if since != 'demo-casual':
            out['games'] = self.games
        return out
