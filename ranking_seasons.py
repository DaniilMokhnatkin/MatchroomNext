"""Calendar for the existing two-month ranked-season schedule."""
from datetime import date

ANCHOR_SEASON = 44
ANCHOR_START = date(2026, 10, 1)


def current_season(today=None):
    today = today if today is not None else date.today()
    months = (today.year - ANCHOR_START.year) * 12 + today.month - ANCHOR_START.month
    return max(1, ANCHOR_SEASON + months // 2)


def season_end_date(season):
    month = ANCHOR_START.year * 12 + ANCHOR_START.month - 1 + 2 * (int(season) - ANCHOR_SEASON + 1)
    year, month_index = divmod(month, 12)
    return date(year, month_index + 1, 1)
