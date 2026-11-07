"""How well a player converts expectations into results.

Pure functions over a ranked match list (newest first). Each row carries the
result, the player's internal rating after the match, the score change and the
opponent's rating. The Elo expectation is the game's own formula:
E = 1 / (1 + 10 ** ((R_opponent - R_player) / 400)).
"""
from __future__ import annotations


def expected(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + 10 ** ((float(opponent) - float(rating)) / 400.0))


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pre_rating(row):
    after = _number(row.get('rating'))
    if after is None:
        return None
    delta = _number(row.get('points_delta'))
    if delta is None:
        return after
    # The sign of a stored delta is not trusted: the result decides it.
    return after - abs(delta) if row['won'] else after + abs(delta)


def efficiency(history, limit: int = 50) -> dict | None:
    """Summarise decided, recorded games; None when nothing is usable."""
    rows = []
    for row in history or []:
        if not isinstance(row, dict) or row.get('inferred') or not isinstance(row.get('won'), bool):
            continue
        pre, opponent = _pre_rating(row), _number(row.get('opponent_rating'))
        if pre is None or opponent is None:
            continue
        rows.append((row['won'], pre, opponent))
        if len(rows) >= limit:
            break
    if not rows:
        return None
    games = len(rows)
    wins = sum(1 for won, _, _ in rows if won)
    losses = games - wins
    chances = [expected(pre, opponent) for _, pre, opponent in rows]
    expected_wins = sum(chances)
    average_opponent = sum(opponent for _, _, opponent in rows) / games
    stronger = [(won, chance) for (won, _, _), chance in zip(rows, chances) if chance < 0.5]
    weaker = [(won, chance) for (won, _, _), chance in zip(rows, chances) if chance >= 0.5]
    return {
        'games': games, 'wins': wins, 'losses': losses,
        'win_rate': wins / games,
        'expected_wins': round(expected_wins, 2),
        'over_expected': round(wins - expected_wins, 2),
        # Classic performance rating: average opposition +/- 400 per net result.
        'performance': round(average_opponent + 400 * (wins - losses) / games),
        'average_opponent': round(average_opponent),
        'upsets': sum(1 for (won, _, _), chance in zip(rows, chances) if won and chance < 0.4),
        'slips': sum(1 for (won, _, _), chance in zip(rows, chances) if not won and chance > 0.6),
        'vs_stronger': {'games': len(stronger), 'wins': sum(1 for won, _ in stronger if won)},
        'vs_weaker': {'games': len(weaker), 'wins': sum(1 for won, _ in weaker if won)},
    }


def win_chance(own: dict | None, other: dict | None) -> tuple[float | None, str | None]:
    """Prefer internal ratings; leaderboard scores sit on a near-constant offset."""
    own, other = own or {}, other or {}
    for key, basis in (('internal_rating', 'internal'), ('score', 'score')):
        a, b = _number(own.get(key)), _number(other.get(key))
        if a is not None and b is not None and a > 0 and b > 0:
            return expected(a, b), basis
    return None, None
