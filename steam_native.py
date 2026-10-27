"""Read-only Steamworks helper, isolated from the local web server process."""
import ctypes as C
import json
import os
from pathlib import Path
import sys
import threading
import time


class Found(C.Structure):
    _fields_ = [('board', C.c_uint64), ('found', C.c_uint8)]


class Downloaded(C.Structure):
    _fields_ = [('board', C.c_uint64), ('entries', C.c_uint64), ('count', C.c_int)]


class Entry(C.Structure):
    _fields_ = [('steam_id', C.c_uint64), ('rank', C.c_int), ('score', C.c_int),
                ('details_count', C.c_int), ('ugc', C.c_uint64)]


def points_to_overtake(player_score, above_score):
    """Return the score increase needed to pass the adjacent entry."""
    try:
        return max(1, int(above_score) - int(player_score) + 1)
    except (TypeError, ValueError):
        return None


def reconcile_global_entry(user_entry, global_entry):
    """Display the global row, but only corroborate matching score responses."""
    if global_entry is None:
        return dict(user_entry, global_verified=False)
    return dict(global_entry, global_verified=user_entry.get('score') == global_entry.get('score'))

# In service mode one process keeps a single Steamworks session for its whole
# life, instead of every request initialising and shutting Steam down again.
_persistent = False
_initialised = False
_shutdown_call = None
_session_lock = threading.RLock()


def _init(fn):
    global _initialised, _shutdown_call
    if _persistent and _initialised:
        return True
    ok = bool(fn('Init', C.c_bool)())
    _initialised = ok
    _shutdown_call = fn('Shutdown', None) if ok else None
    return ok


def _shutdown(fn):
    global _initialised
    if not _persistent:
        fn('Shutdown', None)()
        _initialised = False


def _release_session():
    """Drop the Steam session so Steam stops showing Forts as running."""
    global _initialised, _shutdown_call
    with _session_lock:
        if _initialised and _shutdown_call:
            try:
                _shutdown_call()
            except Exception:
                pass
        _initialised = False
        _shutdown_call = None


def _watch_game():
    from game_process import forts_running
    while True:
        time.sleep(1)
        if _initialised and not forts_running(refresh=True):
            # Shutdown alone leaves Steam showing Forts as running; only the
            # process exiting clears it. The service starts a new helper on demand.
            with _session_lock:
                _release_session()
                sys.stdout.flush()
                os._exit(0)


def serve():
    """Answer JSON-line requests on stdin until it closes; one Steam session throughout."""
    global _persistent
    _persistent = True
    threading.Thread(target=_watch_game, daemon=True).start()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = {}
        try:
            _session_lock.acquire()
            request = json.loads(line)
            op, args = request['op'], request.get('args', [])
            if op == 'activity':
                result = activity()
            elif op == 'radar':
                result = radar(int(args[0]), args[1:])
            elif op == 'lookup':
                season, ids, leader_id = int(args[0]), args[1], args[2] if len(args) > 2 else None
                if not 1 <= season <= 999 or not 1 <= len(ids) <= 64 or any(not s.isdigit() or len(s) != 17 for s in ids):
                    raise ValueError('Некорректный запрос.')
                result = lookup(season, ids, leader_id)
            elif op == 'ping':
                result = {'status': 'ok'}
            else:
                raise ValueError('Unknown operation')
        except Exception as exc:
            result = {'status': 'error', 'message': str(exc), 'players': {}}
        finally:
            _session_lock.release()
        print(json.dumps({'id': request.get('id'), 'result': result}, ensure_ascii=True), flush=True)


def activity():
    """Read public Forts lobby metadata and Steam's current-player counter."""
    from game_process import forts_running
    if not forts_running(refresh=True):
        return {'status': 'paused', 'game_running': False}
    os.environ['SteamAppId'] = '410900'
    os.environ['SteamGameId'] = '410900'
    dll = C.CDLL(str(locate_dll()))

    def fn(name, result, *args):
        f = getattr(dll, 'SteamAPI_' + name)
        f.restype, f.argtypes = result, list(args)
        return f

    if not _init(fn):
        return {'status': 'unavailable', 'message': 'Steam недоступен.'}
    try:
        matchmaking = fn('SteamMatchmaking_v009', C.c_void_p)()
        stats = fn('SteamUserStats_v012', C.c_void_p)()
        utils = fn('SteamUtils_v010', C.c_void_p)()
        if not matchmaking or not stats or not utils:
            raise RuntimeError('Steamworks не вернул необходимые интерфейсы.')

        run_callbacks = fn('RunCallbacks', None)
        is_completed = fn('ISteamUtils_IsAPICallCompleted', C.c_bool,
                          C.c_void_p, C.c_uint64, C.POINTER(C.c_bool))
        get_result = fn('ISteamUtils_GetAPICallResult', C.c_bool,
                        C.c_void_p, C.c_uint64, C.c_void_p, C.c_int,
                        C.c_int, C.POINTER(C.c_bool))

        def wait(handle, result_type, callback):
            if not handle:
                raise RuntimeError('Steam отклонил запрос активности.')
            deadline = time.monotonic() + 12
            failed = C.c_bool()
            while time.monotonic() < deadline:
                run_callbacks()
                if is_completed(utils, handle, C.byref(failed)):
                    response = result_type()
                    if (failed.value or not get_result(
                            utils, handle, C.byref(response), C.sizeof(response),
                            callback, C.byref(failed)) or failed.value):
                        raise RuntimeError('Не удалось прочитать ответ Steam.')
                    return response
                time.sleep(.05)
            raise RuntimeError('Steam не ответил на запрос активности.')

        class LobbyList(C.Structure):
            _fields_ = [('count', C.c_uint32)]

        class CurrentPlayers(C.Structure):
            _fields_ = [('success', C.c_uint8), ('players', C.c_int32)]

        fn('ISteamMatchmaking_AddRequestLobbyListResultCountFilter', None,
           C.c_void_p, C.c_int)(matchmaking, 50)
        lobby_handle = fn('ISteamMatchmaking_RequestLobbyList', C.c_uint64,
                          C.c_void_p)(matchmaking)
        lobby_list = wait(lobby_handle, LobbyList, 510)
        current_handle = fn('ISteamUserStats_GetNumberOfCurrentPlayers',
                            C.c_uint64, C.c_void_p)(stats)
        current = wait(current_handle, CurrentPlayers, 1107)

        get_lobby = fn('ISteamMatchmaking_GetLobbyByIndex', C.c_uint64,
                       C.c_void_p, C.c_int)
        data_count = fn('ISteamMatchmaking_GetLobbyDataCount', C.c_int,
                        C.c_void_p, C.c_uint64)
        data_at = fn('ISteamMatchmaking_GetLobbyDataByIndex', C.c_bool,
                     C.c_void_p, C.c_uint64, C.c_int, C.c_char_p, C.c_int,
                     C.c_char_p, C.c_int)

        ranked_lobbies = set()
        waiting_players = 0
        for index in range(min(int(lobby_list.count), 50)):
            lobby_id = get_lobby(matchmaking, index)
            values = {}
            for data_index in range(max(0, data_count(matchmaking, lobby_id))):
                key = C.create_string_buffer(256)
                value = C.create_string_buffer(1024)
                if data_at(matchmaking, lobby_id, data_index, key, len(key),
                           value, len(value)):
                    name = key.value.decode('utf-8', errors='replace').casefold()
                    values[name] = value.value.decode('utf-8', errors='replace').strip()
            ranked = values.get('ranked', '').casefold() in ('1', 'true', 'yes')
            in_progress = values.get('inprogress', '').casefold() in ('1', 'true', 'yes')
            if ranked and not in_progress:
                ranked_lobbies.add(int(lobby_id))
                try:
                    player_text = values.get('players', '1') or '1'
                    waiting_players += max(0, int(player_text.split('/', 1)[0]))
                except ValueError:
                    waiting_players += 1

        return {
            'status': 'ok',
            'online_players': max(0, int(current.players)) if current.success else None,
            'ranked_waiting_lobbies': len(ranked_lobbies),
            'ranked_waiting_players': waiting_players,
            'truncated': int(lobby_list.count) >= 50,
            'checked_at': time.time(),
        }
    finally:
        _shutdown(fn)


def locate_dll():
    roots = [Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Steam']
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            roots.insert(0, Path(winreg.QueryValueEx(key, 'SteamPath')[0]))
    except (ImportError, OSError):
        pass
    import re
    for root in list(roots):
        try:
            text = (root / 'steamapps/libraryfolders.vdf').read_text(encoding='utf-8')
            roots.extend(Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', text))
        except OSError:
            pass
    for root in roots:
        path = root / 'steamapps/common/Forts/steam_api64.dll'
        if path.is_file():
            return path
    raise RuntimeError('Не найдена установленная Forts. Для рейтинга нужен Steam и установленная игра.')


def ranked_waiting_metadata(values):
    """Require explicit queue evidence; missing metadata is not a waiting lobby."""
    truth = ('1', 'true', 'yes')
    false = ('0', 'false', 'no')
    try:
        players = int(values.get('players', '').split('/', 1)[0])
    except (ValueError, AttributeError):
        return False
    return (values.get('ranked', '').casefold() in truth
            and values.get('inprogress', '').casefold() in false and players == 1)


def radar(season, ids):
    """Read the live top 20, watched scores and accessible friends' game data."""
    from game_process import forts_running
    if not forts_running(refresh=True):
        return {'status': 'paused', 'game_running': False}
    if not 1 <= int(season) <= 999 or len(ids) > 1024 or any(
            len(s) != 17 or not s.isascii() or not s.isdigit() for s in ids):
        raise ValueError('Некорректный запрос радара.')
    os.environ['SteamAppId'] = os.environ['SteamGameId'] = '410900'
    dll = C.CDLL(str(locate_dll()))

    def fn(name, result, *args):
        f = getattr(dll, 'SteamAPI_' + name)
        f.restype, f.argtypes = result, list(args)
        return f

    if not _init(fn):
        return {'status': 'unavailable', 'message': 'Steam недоступен.'}
    try:
        stats = fn('SteamUserStats_v012', C.c_void_p)()
        utils = fn('SteamUtils_v010', C.c_void_p)()
        if not stats or not utils:
            raise RuntimeError('Steamworks не вернул интерфейс рейтинга.')
        run = fn('RunCallbacks', None)
        completed = fn('ISteamUtils_IsAPICallCompleted', C.c_bool,
                       C.c_void_p, C.c_uint64, C.POINTER(C.c_bool))
        get = fn('ISteamUtils_GetAPICallResult', C.c_bool, C.c_void_p,
                 C.c_uint64, C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_bool))
        deadline = time.monotonic() + 24

        def wait(handle, cls, callback):
            failure = C.c_bool()
            until = min(deadline, time.monotonic() + 8)
            if not handle:
                raise RuntimeError('Steam отклонил запрос радара.')
            while time.monotonic() < until:
                run()
                if completed(utils, handle, C.byref(failure)):
                    result = cls()
                    if failure.value or not get(utils, handle, C.byref(result),
                            C.sizeof(result), callback, C.byref(failure)) or failure.value:
                        raise RuntimeError('Неполный ответ рейтинга Steam.')
                    return result
                time.sleep(.05)
            raise RuntimeError('Steam не ответил на запрос радара.')

        found = wait(fn('ISteamUserStats_FindLeaderboard', C.c_uint64,
                        C.c_void_p, C.c_char_p)(stats, f's{season:02d}_Multiplayer1'.encode()), Found, 1104)
        players, top = {}, []
        leaderboard_status = 'unavailable'
        if found.found:
            get_entry = fn('ISteamUserStats_GetDownloadedLeaderboardEntry', C.c_bool,
                           C.c_void_p, C.c_uint64, C.c_int, C.POINTER(Entry),
                           C.POINTER(C.c_int), C.c_int)

            def entries(handle):
                page = wait(handle, Downloaded, 1105)
                if page.board != found.board or not 0 <= page.count <= 256:
                    raise RuntimeError('Steam вернул другую таблицу рейтинга.')
                rows = {}
                for index in range(page.count):
                    entry = Entry()
                    if not get_entry(stats, page.entries, index, C.byref(entry), None, 0):
                        raise RuntimeError('Неполный ответ рейтинга Steam.')
                    if entry.rank > 0:
                        rows[str(entry.steam_id)] = {'rank': entry.rank, 'score': entry.score}
                return rows

            global_rows = entries(fn('ISteamUserStats_DownloadLeaderboardEntries', C.c_uint64,
                                  C.c_void_p, C.c_uint64, C.c_int, C.c_int, C.c_int)(
                                      stats, found.board, 0, 1, 20))
            top = [dict(row, steam_id=sid) for sid, row in global_rows.items() if row['rank'] <= 20]
            top.sort(key=lambda row: row['rank'])
            tracked = sorted(set(ids) - set(global_rows))
            for offset in range(0, len(tracked), 64):
                batch = tracked[offset:offset + 64]
                array = (C.c_uint64 * len(batch))(*(int(s) for s in batch))
                rows = entries(fn('ISteamUserStats_DownloadLeaderboardEntriesForUsers', C.c_uint64,
                               C.c_void_p, C.c_uint64, C.POINTER(C.c_uint64), C.c_int)(
                                   stats, found.board, array, len(batch)))
                players.update({sid: row for sid, row in rows.items() if sid in batch})
            # A single global response is authoritative for current top entries.
            players.update(global_rows)
            leaderboard_status = 'ok'

        presence = {}
        try:
            friends = fn('SteamFriends_v017', C.c_void_p)()
            matchmaking = fn('SteamMatchmaking_v009', C.c_void_p)()
            if not friends or not matchmaking:
                raise RuntimeError('Steam friends unavailable.')

            class FriendGame(C.Structure):
                _fields_ = [('game_id', C.c_uint64), ('ip', C.c_uint32),
                            ('port', C.c_uint16), ('query_port', C.c_uint16),
                            ('lobby_id', C.c_uint64)]

            get_name = fn('ISteamFriends_GetFriendPersonaName', C.c_char_p, C.c_void_p, C.c_uint64)
            request_name = fn('ISteamFriends_RequestUserInformation', C.c_bool,
                              C.c_void_p, C.c_uint64, C.c_bool)
            get_game = fn('ISteamFriends_GetFriendGamePlayed', C.c_bool,
                          C.c_void_p, C.c_uint64, C.POINTER(FriendGame))
            get_data = fn('ISteamMatchmaking_GetLobbyData', C.c_char_p,
                          C.c_void_p, C.c_uint64, C.c_char_p)
            all_ids = sorted(set(ids) | set(players))
            for sid in all_ids:
                request_name(friends, int(sid), True)
            until = time.monotonic() + .6
            while time.monotonic() < until:
                run()
                time.sleep(.05)
            lobbies = {}
            for sid in all_ids:
                name = (get_name(friends, int(sid)) or b'').decode('utf-8', errors='replace')
                if sid in players and name and name != '[unknown]':
                    players[sid]['name'] = name
                game = FriendGame()
                playing = bool(get_game(friends, int(sid), C.byref(game)) and game.game_id == 410900)
                presence[sid] = {'in_forts': True if playing else None,
                                 'searching_ranked': False, 'source': 'steam_friends'}
                if playing and game.lobby_id:
                    lobbies[sid] = game.lobby_id
            if lobbies:
                class LobbyList(C.Structure):
                    _fields_ = [('count', C.c_uint32)]
                # A completed list request supplies fresh public metadata. Do not
                # label a cached or private friend lobby as a confirmed queue.
                fn('ISteamMatchmaking_AddRequestLobbyListDistanceFilter', None,
                   C.c_void_p, C.c_int)(matchmaking, 3)
                fn('ISteamMatchmaking_AddRequestLobbyListResultCountFilter', None,
                   C.c_void_p, C.c_int)(matchmaking, 100)
                listed = wait(fn('ISteamMatchmaking_RequestLobbyList', C.c_uint64,
                                 C.c_void_p)(matchmaking), LobbyList, 510)
                get_lobby = fn('ISteamMatchmaking_GetLobbyByIndex', C.c_uint64,
                               C.c_void_p, C.c_int)
                public_lobbies = {get_lobby(matchmaking, index) for index in range(min(listed.count, 100))}
                for sid, lobby_id in lobbies.items():
                    if lobby_id not in public_lobbies:
                        continue
                    values = {key: (get_data(matchmaking, lobby_id, key.encode()) or b'').decode(
                              'utf-8', errors='replace') for key in ('ranked', 'inprogress', 'players')}
                    presence[sid]['searching_ranked'] = ranked_waiting_metadata(values)
        except (AttributeError, RuntimeError):
            # Scores remain useful even when friend presence is unavailable.
            pass
        return {'status': 'ok', 'season': season, 'checked_at': time.time(),
                'leaderboard_status': leaderboard_status, 'top': top,
                'players': players, 'presence': presence}
    finally:
        _shutdown(fn)


def lookup(season, ids, leader_id=None):
    from game_process import forts_running
    if not forts_running(refresh=True):
        return {'status': 'paused', 'game_running': False, 'players': {},
                'message': 'Forts закрыта. Проверка рейтинга приостановлена.'}
    os.environ['SteamAppId'] = '410900'
    os.environ['SteamGameId'] = '410900'
    dll = C.CDLL(str(locate_dll()))
    def fn(name, result, *args):
        f = getattr(dll, 'SteamAPI_' + name)
        f.restype, f.argtypes = result, list(args)
        return f
    if not _init(fn):
        raise RuntimeError('Steam недоступен. Запустите Steam под аккаунтом с Forts и перезапустите START.cmd.')
    try:
        stats = fn('SteamUserStats_v012', C.c_void_p)()
        utils = fn('SteamUtils_v010', C.c_void_p)()
        if not stats or not utils:
            raise RuntimeError('Не удалось получить интерфейсы Steamworks.')
        run = fn('RunCallbacks', None)
        completed = fn('ISteamUtils_IsAPICallCompleted', C.c_bool, C.c_void_p, C.c_uint64, C.POINTER(C.c_bool))
        get = fn('ISteamUtils_GetAPICallResult', C.c_bool, C.c_void_p, C.c_uint64, C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_bool))
        deadline = time.monotonic() + 16
        def wait(handle, cls, callback):
            if not handle:
                raise RuntimeError('Steam отклонил запрос рейтинга.')
            failure = C.c_bool()
            while time.monotonic() < deadline:
                run()
                if completed(utils, handle, C.byref(failure)):
                    out = cls()
                    if failure.value or not get(utils, handle, C.byref(out), C.sizeof(out), callback, C.byref(failure)) or failure.value:
                        raise RuntimeError('Ошибка чтения рейтинга Steam.')
                    return out
                time.sleep(.05)
            raise RuntimeError('Steam не ответил вовремя. Попробуйте обновить данные.')
        handle = fn('ISteamUserStats_FindLeaderboard', C.c_uint64, C.c_void_p, C.c_char_p)(stats, f's{season:02d}_Multiplayer1'.encode())
        found = wait(handle, Found, 1104)
        if not found.found:
            return {'status': 'unavailable', 'message': f'Таблица 1v1 сезона {season} не найдена.', 'players': {}}
        count = fn('ISteamUserStats_GetLeaderboardEntryCount', C.c_int, C.c_void_p, C.c_uint64)(stats, found.board)
        array = (C.c_uint64 * len(ids))(*(int(s) for s in ids))
        handle = fn('ISteamUserStats_DownloadLeaderboardEntriesForUsers', C.c_uint64, C.c_void_p, C.c_uint64, C.POINTER(C.c_uint64), C.c_int)(stats, found.board, array, len(ids))
        downloaded = wait(handle, Downloaded, 1105)
        if downloaded.board != found.board:
            raise RuntimeError('Steam returned a different leaderboard.')
        get_entry = fn('ISteamUserStats_GetDownloadedLeaderboardEntry', C.c_bool, C.c_void_p, C.c_uint64, C.c_int, C.POINTER(Entry), C.POINTER(C.c_int), C.c_int)
        players = {}
        for i in range(downloaded.count):
            entry = Entry()
            if not get_entry(stats, downloaded.entries, i, C.byref(entry), None, 0):
                raise RuntimeError('Steam вернул неполные данные рейтинга.')
            if str(entry.steam_id) in ids and entry.rank > 0:
                players[str(entry.steam_id)] = {'rank': entry.rank, 'score': entry.score}
        # A user-specific lookup alone is not sufficient evidence for a match
        # delta. Cross-check by Steam ID in a separately downloaded global range.
        # Bound this extra work to match participants / the owner card.
        verified_ids = ids if len(ids) <= 2 else ([leader_id] if leader_id else [])
        for steam_id in verified_ids:
            if steam_id not in players:
                continue
            user_entry = players[steam_id]
            global_entry = None
            try:
                rank = user_entry['rank']
                handle = fn('ISteamUserStats_DownloadLeaderboardEntries', C.c_uint64,
                            C.c_void_p, C.c_uint64, C.c_int, C.c_int, C.c_int)(
                                stats, found.board, 0, max(1, rank - 32), rank + 32)
                page = wait(handle, Downloaded, 1105)
                if page.board != found.board:
                    raise RuntimeError('Steam returned a different leaderboard.')
                for index in range(page.count):
                    entry = Entry()
                    if not get_entry(stats, page.entries, index, C.byref(entry), None, 0):
                        raise RuntimeError('Incomplete global leaderboard response.')
                    if str(entry.steam_id) == steam_id and entry.rank > 0:
                        global_entry = {'rank': entry.rank, 'score': entry.score}
            except Exception:
                global_entry = None
            players[steam_id] = reconcile_global_entry(user_entry, global_entry)
        if leader_id and leader_id in players:
            own = players[leader_id]
            if own['rank'] == 1:
                own['leader_above_status'] = 'top'
            else:
                # GlobalAroundUser always includes the requested user's row.
                # Asking for one preceding entry avoids rank-index assumptions
                # when the board contains tied scores.
                try:
                    around = fn('ISteamUserStats_DownloadLeaderboardEntries', C.c_uint64,
                                C.c_void_p, C.c_uint64, C.c_int, C.c_int, C.c_int)(
                                    stats, found.board, 1, -1, 0)
                    neighborhood = wait(around, Downloaded, 1105)
                    rows = []
                    for i in range(neighborhood.count):
                        entry = Entry()
                        if not get_entry(stats, neighborhood.entries, i, C.byref(entry), None, 0):
                            raise RuntimeError('Steam вернул неполные данные места выше.')
                        rows.append(entry)
                    own_index = next((i for i, entry in enumerate(rows)
                                      if str(entry.steam_id) == leader_id), None)
                    above = rows[own_index - 1] if own_index is not None and own_index > 0 else None
                    points_needed = points_to_overtake(own.get('score'), above.score) if above else None
                    if above and above.rank > 0 and points_needed is not None:
                        own['leader_above'] = {'rank': above.rank, 'score': above.score}
                        own['points_to_overtake'] = points_needed
                        own['leader_above_status'] = 'ok'
                    else:
                        own['leader_above_status'] = 'unavailable'
                except Exception:
                    own['leader_above_status'] = 'unavailable'
        return {'status': 'ok', 'season': season, 'mode': '1v1', 'total': count,
                'board_id': str(found.board), 'players': players}
    finally:
        _shutdown(fn)


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2 and sys.argv[1] == '--service':
            serve()
            raise SystemExit(0)
        if len(sys.argv) >= 3 and sys.argv[1] == '--radar':
            print(json.dumps(radar(int(sys.argv[2]), sys.argv[3:]), ensure_ascii=True), flush=True)
            raise SystemExit(0)
        if len(sys.argv) == 2 and sys.argv[1] == '--activity':
            print(json.dumps(activity(), ensure_ascii=True), flush=True)
            raise SystemExit(0)
        season = int(sys.argv[1]); arguments = sys.argv[2:]
        leader_id = None
        if '--leader-id' in arguments:
            index = arguments.index('--leader-id')
            if index + 1 >= len(arguments):
                raise ValueError('Не указан Steam ID для поиска соседнего места.')
            leader_id = arguments[index + 1]
            del arguments[index:index + 2]
        ids = arguments
        if not 1 <= season <= 999 or not 1 <= len(ids) <= 64 or any(not s.isdigit() or len(s) != 17 for s in ids):
            raise ValueError('Некорректный запрос.')
        if leader_id and (leader_id not in ids or not leader_id.isdigit() or len(leader_id) != 17):
            raise ValueError('Некорректный Steam ID для поиска соседнего места.')
        result = lookup(season, ids, leader_id)
    except Exception as exc:
        result = {'status': 'error', 'message': str(exc), 'players': {}}
    print(json.dumps(result, ensure_ascii=True), flush=True)
