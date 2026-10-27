"""Local HTTP server for the Matchroom Next client.

Serves the static client from ``web/`` and the JSON API. Everything binds to
127.0.0.1; API calls require the per-session token from the boot block, and
pages are only served for a localhost Host header (DNS-rebinding guard).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from forts_replay import MAX_REPLAY, ReplayError, parse_replay, valid_steam_id

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
ASSETS = ROOT / 'assets'

STATIC_TYPES = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.json': 'application/json; charset=utf-8',
    '.woff2': 'font/woff2',
}
DEMO_ASSETS = {'map-stalactites.svg', 'map-foundry.svg', 'avatar-self.svg', 'avatar-opponent.svg'}

PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https://*.steamstatic.com; connect-src 'self'; "
            "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def _static_path(relative: str) -> Path | None:
    """Resolve a /app/ path inside web/, refusing anything that escapes it."""
    if not relative or '\\' in relative or relative.startswith('/'):
        return None
    candidate = (WEB / relative).resolve()
    try:
        candidate.relative_to(WEB.resolve())
    except ValueError:
        return None
    if candidate.suffix.lower() not in STATIC_TYPES or not candidate.is_file():
        return None
    return candidate


def make_server(initial: list[dict], default_my_id: str | None = None, history=None,
                app_version='dev', installer_path=None, autostart_getter=None,
                autostart_setter=None, radar=None, port=0, lobbies=None,
                window_mode=False, on_window_state=None, notifier=None, update_parent_pid=None):
    token = secrets.token_urlsafe(32)
    template = (WEB / 'index.html').read_text(encoding='utf-8')
    from commander_names import COMMANDER_NAMES

    def page():
        boot = {
            'token': token,
            'version': app_version,
            'language': getattr(history, 'language', 'ru') if history else 'ru',
            'window': bool(window_mode),
            'commanderNames': COMMANDER_NAMES,
        }
        data = json.dumps(boot, ensure_ascii=True).replace('<', r'<')
        return template.replace('__BOOT__', data)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def send_bytes(self, status, content, content_type, cache='no-store', csp=API_CSP, extra=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', csp)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(content)

        def respond(self, status: int, value, html: bool = False, download: bool = False):
            if html:
                self.send_bytes(status, value.encode('utf-8'), 'text/html; charset=utf-8', csp=PAGE_CSP)
                return
            extra = {'Content-Disposition': 'attachment; filename="forts-players.json"'} if download else None
            self.send_bytes(status, json.dumps(value, ensure_ascii=True).encode('utf-8'),
                            'application/json; charset=utf-8', extra=extra)

        def respond_installer(self):
            path = Path(installer_path).resolve() if installer_path else None
            if not path or not path.is_file() or path.suffix.lower() != '.exe':
                self.respond(404, {'error': 'Установщик доступен только в установленной версии.'})
                return
            size = path.stat().st_size
            if size < 1024 or size > 256 * 1024 * 1024:
                self.respond(500, {'error': 'Установщик имеет некорректный размер.'})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.microsoft.portable-executable')
            self.send_header('Content-Length', str(size))
            self.send_header('Content-Disposition', f'attachment; filename="FortsMatchroom-Setup-v{app_version}.exe"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)

        def state(self, since=None):
            value = history.snapshot(since) if history else {'matches': initial}
            if not history:
                from ranking_seasons import current_season, season_end_date
                value['active_season'] = current_season()
                value['season_end'] = season_end_date(value['active_season']).isoformat()
            value['app_version'] = app_version
            value['autostart_available'] = bool(installer_path and autostart_setter)
            value['portable'] = True
            value['autostart_enabled'] = bool(autostart_getter and autostart_getter())
            value['installer_available'] = bool(installer_path and Path(installer_path).is_file())
            from steam_data import activity
            value['game_activity'] = activity()
            from steam_service import throttle_state
            value['steam_throttle'] = throttle_state()
            if radar:
                value['ranked_radar'] = radar.snapshot(history.owner_steam_id if history else '')
            if lobbies:
                value['lobbies'] = lobbies.snapshot()
            if notifier:
                value['notifications'] = notifier.snapshot()
            return value

        def authorized(self):
            if self.headers.get('X-Forts-Token') == token:
                self.server.last_client_seen = time.monotonic()
                return True
            return False

        def local_host(self):
            return self.headers.get('Host') in (
                f'127.0.0.1:{self.server.server_port}',
                f'localhost:{self.server.server_port}')

        def read_json(self, limit):
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= limit:
                raise ValueError('Некорректный размер запроса.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Ожидался объект.')
            return body

        def serve_file(self, path: Path, cache='no-cache'):
            try:
                data = path.read_bytes()
            except OSError:
                self.respond(404, {'error': 'Not found'})
                return
            self.send_bytes(200, data, STATIC_TYPES[path.suffix.lower()], cache=cache)

        def do_GET(self):
            parsed = urlsplit(self.path)
            route = parsed.path
            if route.startswith('/app/') and self.local_host():
                path = _static_path(route.removeprefix('/app/'))
                if path:
                    self.serve_file(path)
                else:
                    self.respond(404, {'error': 'Not found'})
                return
            if route.startswith('/assets/') and self.local_host():
                name = route.removeprefix('/assets/')
                if name not in DEMO_ASSETS:
                    self.respond(404, {'error': 'Not found'})
                    return
                self.serve_file(ASSETS / name, cache='private, max-age=3600')
                return
            if route == '/favicon.ico' and self.local_host():
                self.serve_file(ASSETS / 'matchroom-next.ico', cache='public, max-age=86400')
                return
            if route == '/forts_icon.png' and self.local_host():
                self.serve_file(ROOT / 'forts_icon.png', cache='public, max-age=86400')
                return
            if route == '/api/map-image' and parse_qs(parsed.query).get('token', [None])[0] == token:
                try:
                    name = parse_qs(parsed.query).get('name', [''])[0]
                    if not name or len(name) > 120 or '/' in name or '\\' in name or name in ('.', '..'):
                        raise ValueError('Invalid map name')
                    from steam_native import locate_dll
                    maps = locate_dll().parent / 'data' / 'maps'
                    picture = maps / name / f'preview-{name}-3.jpg'
                    if not picture.is_file():
                        picture = maps / name / f'preview-{name}-1.jpg'
                    data = picture.read_bytes()
                    if len(data) > 4 * 1024 * 1024:
                        raise ValueError('Map image is too large')
                    self.send_bytes(200, data, 'image/jpeg', cache='private, max-age=3600')
                except (OSError, RuntimeError, ValueError):
                    self.respond(404, {'error': 'Map image unavailable'})
                return
            if route == '/' and self.local_host():
                self.respond(200, page(), html=True)
                return
            if not self.authorized():
                self.respond(404, {'error': 'Not found'})
                return
            args = parse_qs(parsed.query)
            if route == '/api/update':
                try:
                    from update_service import check
                    self.respond(200, check(app_version))
                except Exception:
                    self.respond(503, {'error': 'Update check failed'})
                return
            if route == '/api/ping':
                self.respond(200, {'app': 'Forts Matchroom', 'version': app_version})
            elif route == '/api/state' and history:
                # Every poll says whether the window has focus: toasts wait for the background.
                if notifier and 'focus' in args:
                    notifier.client_focus(args['focus'][0] == '1')
                self.respond(200, self.state(args.get('since', [None])[0]))
            elif route in ('/api/lobbies', '/api/lobbies/export') and lobbies:
                self.respond(200, lobbies.snapshot(), download=route.endswith('/export'))
            elif route == '/api/activity':
                from steam_data import activity
                self.respond(200, activity(force=args.get('refresh', ['0'])[0] == '1'))
            elif route == '/api/radar' and radar:
                if args.get('refresh', ['0'])[0] == '1':
                    radar.refresh()
                value = radar.snapshot(history.owner_steam_id if history else '')
                value['language'] = history.language if history else 'ru'
                self.respond(200, value)
            elif route == '/api/installer':
                self.respond_installer()
            elif route == '/api/encounters' and history:
                self.respond(200, history.encounters(args.get('own', [''])[0], args.get('opponent', [''])[0],
                                                     args.get('exclude', [None])[0]))
            elif route == '/api/profile':
                try:
                    steam_id = args.get('id', [''])[0]
                    if not re.fullmatch(r'[0-9]{17}', steam_id) or not valid_steam_id(int(steam_id)):
                        raise ValueError('Некорректный Steam ID.')
                    from steam_data import profile
                    result = profile(steam_id, force=args.get('retry', ['0'])[0] == '1')
                    self.respond(200, {'profiles': {steam_id: result}})
                except ValueError as exc:
                    self.respond(400, {'error': str(exc)})
            elif route == '/api/steam':
                self.steam(args)
            elif route == '/api/initial':
                self.respond(200, history.matches() if history else initial)
            else:
                self.respond(404, {'error': 'Not found'})

        def steam(self, args):
            try:
                ids = sorted(set(args.get('ids', [''])[0].split(',')))
                from ranking_seasons import current_season
                season = int(args.get('season', [str(history.active_season() if history else current_season())])[0])
                leader_id = args.get('leader_id', [''])[0] or None
                if not 1 <= season <= 999 or not 1 <= len(ids) <= 64 or any(
                        not re.fullmatch(r'[0-9]{17}', s) or not valid_steam_id(int(s)) for s in ids):
                    raise ValueError('Некорректный Steam ID или сезон.')
                if leader_id and (leader_id not in ids or not re.fullmatch(r'[0-9]{17}', leader_id)
                                  or not valid_steam_id(int(leader_id))):
                    raise ValueError('Steam ID для поиска места выше должен входить в список игроков.')
                from steam_data import enrich
                result = enrich(ids, season, leader_id, args.get('refresh', ['0'])[0] == '1')
                if history:
                    board = result.get('leaderboard', {})
                    history.record_ranking_snapshot(season, board, ids)
                    if leader_id and board.get('status') == 'ok':
                        history.record_leaderboard_gap(season, leader_id, board.get('players', {}).get(leader_id),
                                                       board.get('checked_at'))
                self.respond(200, result)
            except ValueError as exc:
                self.respond(400, {'error': str(exc)})

        def do_POST(self):
            # A regular attachment response also works in embedded browsers
            # that do not implement downloads from blob: URLs.
            if self.path == '/api/export':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= size <= 16 * 1024 * 1024:
                        raise ReplayError('Слишком большой отчёт.')
                    form = parse_qs(self.rfile.read(size).decode('utf-8'))
                    if form.get('token', [None])[0] != token:
                        self.respond(403, {'error': 'Forbidden'})
                        return
                    self.respond(200, json.loads(form.get('report', [''])[0]), download=True)
                except (ValueError, OSError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if not self.authorized():
                self.respond(403, {'error': 'Forbidden'})
                return
            route = urlsplit(self.path).path
            if route.startswith('/api/lobbies/') and lobbies:
                try:
                    body = self.read_json(4096)
                    if route == '/api/lobbies/refresh':
                        result = lobbies.refresh(body.get('once', False))
                    elif route == '/api/lobbies/join':
                        result = lobbies.join(body.get('id'))
                    elif route == '/api/lobbies/settings':
                        result = lobbies.configure(body.get('notify'), body.get('favorite'), body.get('forget_last', False))
                    elif route == '/api/lobbies/restart-game' and not getattr(history, 'demo', False):
                        result = lobbies.restart_game()
                    elif route == '/api/lobbies/sound':
                        result = lobbies.sound()
                    elif route == '/api/lobbies/demo' and getattr(history, 'demo', False):
                        result = lobbies.scenario(body.get('mode'))
                    else:
                        self.respond(404, {'error': 'Not found'})
                        return
                    self.respond(200, result)
                except (ValueError, TypeError, OSError, RuntimeError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route == '/api/update':
                try:
                    from update_service import install
                    result = install(app_version, installer_path, update_parent_pid)
                    self.respond(200, {'version': result})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                except Exception:
                    self.respond(503, {'error': 'Update failed'})
                return
            if route == '/api/window':
                try:
                    body = self.read_json(1024)
                    rect = {key: int(body[key]) for key in ('x', 'y', 'width', 'height')}
                    if not (480 <= rect['width'] <= 10000 and 360 <= rect['height'] <= 10000):
                        raise ValueError('Invalid window size')
                    rect['maximized'] = bool(body.get('maximized'))
                    if on_window_state:
                        on_window_state(rect)
                    self.respond(200, {'ok': True})
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route == '/api/notifications/test':
                try:
                    if not notifier:
                        raise ValueError('Уведомления Windows недоступны в этой версии.')
                    self.respond(200, notifier.test())
                except ValueError as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route == '/api/account' and history:
                try:
                    body = self.read_json(1024)
                    history.select_account(body.get('mode'), body.get('steam_id', ''))
                    self.respond(200, self.state())
                except (ValueError, TypeError, AttributeError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route == '/api/demo-scenario' and getattr(history, 'demo', False):
                try:
                    history.set_empty(bool(self.read_json(1024).get('empty')))
                    self.respond(200, self.state())
                except (ValueError, TypeError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route in ('/api/watch', '/api/import-paths', '/api/owner', '/api/autostart',
                         '/api/onboarding', '/api/language', '/api/radar') and history:
                try:
                    body = self.read_json(65536)
                    if route == '/api/watch':
                        history.configure(body.get('enabled'), body.get('folders'))
                    elif route == '/api/radar':
                        if not radar:
                            raise ValueError('Радар недоступен.')
                        radar.configure(body.get('enabled'), body.get('notify'))
                    elif route == '/api/owner':
                        history.set_owner_id(body.get('steam_id', ''))
                    elif route == '/api/autostart':
                        if not isinstance(body.get('enabled'), bool) or not autostart_setter:
                            raise ValueError('Автозапуск недоступен в этой версии.')
                        autostart_setter(body['enabled'])
                    elif route == '/api/onboarding':
                        history.set_onboarding_complete(bool(body.get('complete', True)))
                    elif route == '/api/language':
                        history.set_language(body.get('language'))
                    else:
                        paths = body.get('paths')
                        if not isinstance(paths, list) or len(paths) > 32:
                            raise ValueError('Некорректный список файлов.')
                        imported = []
                        for value in paths:
                            path = Path(value)
                            if path.suffix.lower() != '.fwr':
                                raise ValueError('Ожидался .fwr')
                            imported.append(history.import_file(path)[0])
                        self.respond(200, imported)
                        return
                    self.respond(200, self.state())
                except (ValueError, OSError, TypeError) as exc:
                    self.respond(400, {'error': str(exc)})
                return
            if route == '/api/stop':
                self.respond(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if route != '/api/analyze':
                self.respond(404, {'error': 'Not found'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 17 <= size <= MAX_REPLAY:
                    raise ReplayError('Недопустимый размер реплея.')
                args = parse_qs(urlsplit(self.path).query)
                name = args.get('name', ['replay.fwr'])[0][:1024]
                my_id = args.get('my_id', [default_my_id])[0]
                if my_id and (not my_id.isascii() or not my_id.isdecimal() or not valid_steam_id(int(my_id))):
                    raise ReplayError('Введите корректный Steam ID64 (17 цифр).')
                data = self.rfile.read(size)
                result = history.add(data, name)[0] if history else parse_replay(data, name, my_id)
                self.respond(200, result)
            except (ValueError, OSError) as exc:
                self.respond(400, {'error': str(exc)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app_token = token
    server.last_client_seen = None
    return server
