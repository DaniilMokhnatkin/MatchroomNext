"""Public Steam profiles plus read-only current-season Steamworks rankings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import html
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from game_process import forts_running

_profiles = {}
_rankings = {}
_lock = threading.Lock()
_native_lock = threading.Lock()
RANKING_CACHE_SECONDS = 60
ACTIVITY_CACHE_SECONDS = 30
_activity_lock = threading.Lock()
_activity = {'status': 'checking', 'online_players': None,
             'ranked_waiting_lobbies': None, 'ranked_waiting_players': None,
             'checked_at': None}
_activity_started = 0.0
_activity_running = False


def profile_presence(root):
    """Only a current game field can establish presence; offline stays unknown."""
    if (root.findtext('onlineState') or '').casefold() != 'in-game':
        return None
    link = root.findtext('inGameInfo/gameLink') or ''
    parsed = urlsplit(link)
    if parsed.hostname in ('steamcommunity.com', 'store.steampowered.com'):
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 2 and parts[0] == 'app' and parts[1].isdigit():
            return parts[1] == '410900'
    # Older XML profiles expose the current game only in stateMessage.
    import re
    message = html.unescape(re.sub(r'<[^>]+>', '\n', root.findtext('stateMessage') or ''))
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return True if len(lines) >= 2 and lines[-1] == 'Forts' else None


def profile(steam_id, force=False):
    with _lock:
        cached = _profiles.get(steam_id)
        cache_seconds = 600 if cached and cached[1].get('status') == 'ok' else 120
        if cached and not force and time.monotonic() - cached[0] < cache_seconds:
            return cached[1]
    from steam_service import community
    if not community.wait_turn():
        # Paused after a refusal, or too many waiting: keep what we know, ask nothing.
        return cached[1] if cached else {'status': 'unavailable', 'name': None, 'avatar': None, 'throttled': True}
    try:
        url = f'https://steamcommunity.com/profiles/{steam_id}/?xml=1'
        with urlopen(Request(url, headers={'User-Agent': 'FortsReplayReader/2.0'}), timeout=8) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Ответ Steam слишком большой')
        root = ET.fromstring(raw)
        if root.findtext('steamID64') != steam_id:
            raise ValueError('Steam не вернул профиль')
        avatar = root.findtext('avatarFull') or ''
        parsed = urlsplit(avatar)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.steamstatic.com'):
            avatar = None
        result = {'status': 'ok', 'name': root.findtext('steamID'), 'avatar': avatar,
                  'in_forts': profile_presence(root), 'presence_checked_at': time.time()}
        community.succeeded()
    except HTTPError as error:
        if error.code in (401, 403, 429, 503):
            retry = error.headers.get('Retry-After') if error.headers else None
            community.refused(float(retry) if retry and retry.isdigit() else None)
        result = cached[1] if cached and cached[1].get('status') == 'ok' else {'status': 'unavailable', 'name': None, 'avatar': None}
    except Exception:
        result = cached[1] if cached and cached[1].get('status') == 'ok' else {'status': 'unavailable', 'name': None, 'avatar': None}
    with _lock:
        _profiles[steam_id] = (time.monotonic(), result)
    return result


def rankings(season, ids, force=False, leader_id=None):
    key = (season, tuple(sorted(ids)), leader_id)
    if not forts_running():
        cached = _rankings.get(key)
        return (dict(cached[1], game_running=False, paused=True) if cached else
                {'status': 'paused', 'game_running': False, 'players': {},
                 'message': 'Forts закрыта. Проверка рейтинга приостановлена.'})
    with _native_lock:
        if not forts_running(refresh=True):
            cached = _rankings.get(key)
            return (dict(cached[1], game_running=False, paused=True) if cached else
                    {'status': 'paused', 'game_running': False, 'players': {},
                     'message': 'Forts закрыта. Проверка рейтинга приостановлена.'})
        cached = _rankings.get(key)
        if cached and not force and time.monotonic() - cached[0] < RANKING_CACHE_SECONDS:
            return cached[1]
        try:
            from steam_service import native
            result = native.call('lookup', [season, list(ids), leader_id], timeout=24)
            if result.get('status') == 'unavailable' and result.get('retry_in'):
                result = {'status': 'error', 'players': {},
                          'message': 'Steam временно ограничил запросы. Повторим через ' + str(result['retry_in']) + ' с.'}
        except Exception:
            result = {'status': 'error', 'message': 'Не удалось прочитать рейтинг. Проверьте, что Steam запущен и Forts установлена.', 'players': {}}
        result['checked_at'] = datetime.now(timezone.utc).isoformat()
        result['game_running'] = result.get('status') != 'paused'
        if result['status'] == 'ok':
            _rankings[key] = (time.monotonic(), result)
        return result


def enrich(ids, season, leader_id=None, force=False):
    with ThreadPoolExecutor(max_workers=5) as pool:
        rank_future = pool.submit(rankings, season, ids, force, leader_id)
        profiles = dict(zip(ids, pool.map(profile, ids)))
        rank = rank_future.result()
    return {'profiles': profiles, 'leaderboard': rank}


def activity(force=False):
    """Return cached game activity immediately; refresh in a background worker."""
    global _activity, _activity_started, _activity_running

    def refresh():
        global _activity, _activity_started, _activity_running
        try:
            with _native_lock:
                if not forts_running(refresh=True):
                    raise RuntimeError('Forts is closed; use the public online counter.')
                from steam_service import native
                result = native.call('activity', [], timeout=18)
                if result.get('status') == 'error':
                    raise RuntimeError(result.get('message'))
        except Exception:
            result = {'status': 'unavailable', 'online_players': None,
                      'ranked_waiting_lobbies': None, 'ranked_waiting_players': None,
                      'checked_at': time.time()}
        if result.get('online_players') is None:
            try:
                request = Request(
                    'https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=410900',
                    headers={'User-Agent': 'FortsMatchroom/1.0'})
                from steam_service import webapi
                if not webapi.wait_turn():
                    raise RuntimeError('paused')
                try:
                    with urlopen(request, timeout=6) as response:
                        payload = response.read(65537)
                except HTTPError as error:
                    if error.code in (403, 429, 503):
                        webapi.refused()
                    raise
                webapi.succeeded()
                if len(payload) > 65536:
                    raise ValueError('Steam returned an oversized response.')
                count = int(json.loads(payload).get('response', {}).get('player_count'))
                if count < 0:
                    raise ValueError('Steam returned an invalid player count.')
                result['online_players'] = count
                if result.get('status') != 'ok':
                    result['status'] = 'partial'
            except Exception:
                pass
        with _activity_lock:
            result['game_running'] = forts_running()
            _activity = result
            _activity_started = time.monotonic()
            _activity_running = False

    with _activity_lock:
        game_running = forts_running()
        interval = ACTIVITY_CACHE_SECONDS if game_running else 60
        if not game_running:
            _activity = dict(_activity, status='partial' if _activity.get('online_players') is not None else 'paused',
                             ranked_waiting_lobbies=None, ranked_waiting_players=None, game_running=False)
        just_opened = game_running and _activity.get('game_running') is False
        if not _activity_running and (force or just_opened or time.monotonic() - _activity_started >= interval):
            _activity_running = True
            _activity = dict(_activity, status='checking')
            threading.Thread(target=refresh, name='forts-activity', daemon=True).start()
        return dict(_activity)
