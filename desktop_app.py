"""Desktop entry point: one instance, an own app window, and the local server."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
import webbrowser
from urllib.request import Request, urlopen

APP_VERSION = 'next-0.10'
AUTOSTART_NAME = 'FortsMatchroomNext'


def autostart_enabled(executable=None):
    if os.name != 'nt' or not executable:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
        return str(Path(executable).resolve()).lower() in value.lower()
    except OSError:
        return False


def set_autostart(enabled, executable=None):
    if os.name != 'nt' or not executable:
        raise RuntimeError('Автозапуск доступен только в установленной версии Windows.')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                              f'"{Path(executable).resolve()}" --headless')
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    return autostart_enabled(executable)


def process_alive(pid):
    if os.name != 'nt':
        return True
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        return False
    try:
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)


def watch_stdin(server):
    """Closing the shell's pipe is the clean way to ask this service to stop."""
    try:
        while sys.stdin and sys.stdin.read(1024):
            pass
    except (OSError, ValueError):
        pass
    logging.info('Shell closed the control pipe; stopping Next.')
    server.shutdown()


def watch_parent(pid, server):
    """The native shell owns this process: when it is gone, stop too."""
    while process_alive(pid):
        time.sleep(2)
    logging.info('Shell process %s is gone; stopping Next.', pid)
    server.shutdown()


def app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def run(files=(), my_id=None, data_dir=None, no_browser=False, no_tray=False,
        installer_path=None, app_version=APP_VERSION, use_browser=False, shell=False, parent_pid=None):
    from match_history import History
    from forts_replay import make_server
    import app_window
    directory = Path(data_dir) if data_dir else app_directory() / 'MatchroomNextData'
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / 'matchroom.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    mutex = None
    statefile = directory / 'session.json'
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        key = hashlib.sha256(str(directory.resolve()).lower().encode()).hexdigest()[:24]
        mutex = kernel.CreateMutexW(None, False, 'Local\\FortsMatchroomNext-' + key)
        if ctypes.get_last_error() == 183 and shell:
            print('ERROR already-running', flush=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
            return 3
        if ctypes.get_last_error() == 183:
            try:
                # The first process may still be starting its server.
                for attempt in range(30):
                    try:
                        state = json.loads(statefile.read_text(encoding='utf-8'))
                        headers = {'X-Forts-Token': state['token']}
                        with urlopen(Request(state['url'] + 'api/ping', headers=headers), timeout=1) as response:
                            running = json.load(response)
                            if running.get('app') != 'Forts Matchroom':
                                raise ValueError('Unexpected local server')
                            if running.get('version') != app_version:
                                raise RuntimeError('Уже открыта другая версия Matchroom Next. '
                                                   'Заверши её через Настройки → Завершить Next и запусти новую версию.')
                        if files:
                            body = json.dumps({'paths': [str(Path(p).resolve()) for p in files]}).encode()
                            with urlopen(Request(state['url'] + 'api/import-paths', data=body, headers=headers), timeout=15):
                                pass
                        if not no_browser and (use_browser or not app_window.launch(state['url'], directory / 'window-profile')):
                            webbrowser.open(state['url'])
                        return 0
                    except (OSError, ValueError, KeyError):
                        time.sleep(.2)
                raise RuntimeError('Matchroom Next уже запускается или не отвечает. Закрой Next через настройки; '
                                   'если окно недоступно, заверши процесс Matchroom Next в Диспетчере задач.')
            finally:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(mutex)
    history = History(directory)
    from ranked_radar import RankedRadar
    radar = RankedRadar(directory, owner_getter=lambda: history.owner_steam_id)
    from lobby_service import LobbyService
    lobbies = LobbyService(directory)
    icon = None
    server = None
    window = None
    # With the native shell the window belongs to it; the service only reports the URL.
    window_mode = not shell and not no_browser and not use_browser and app_window.find_app_browser() is not None

    def open_route(route):
        if shell:
            print('OPEN ' + route, flush=True)
            return
        # A toast click: raise the existing window (the client then switches
        # the view), or open a new one straight on the right section.
        if window and window.poll() is None and app_window.focus(window.pid):
            return
        target = url + '#/' + route
        if use_browser or not app_window.launch(target, directory / 'window-profile'):
            webbrowser.open(target)

    from windows_notify import WindowsNotifier
    # One switch in Settings: the radar keeps it as its "notify" flag.
    notifier = WindowsNotifier(directory, enabled=lambda: radar.notify,
                               language=lambda: history.language, opener=open_route)
    radar.notifier = notifier
    lobbies.notifier = notifier
    try:
        for path in files:
            try:
                history.import_file(Path(path))
            except (ValueError, OSError):
                logging.exception('Could not import %s', path)
        executable = str(Path(installer_path).resolve()) if installer_path else None
        server = make_server(
            [], my_id, history, app_version=app_version, installer_path=executable,
            radar=radar, lobbies=lobbies, notifier=notifier,
            autostart_getter=lambda: autostart_enabled(executable),
            autostart_setter=lambda value: set_autostart(value, executable),
            window_mode=window_mode, update_parent_pid=parent_pid,
            on_window_state=lambda rect: app_window.save_placement(directory, rect))
        url = f'http://127.0.0.1:{server.server_port}/'
        temporary = statefile.with_suffix('.tmp')
        temporary.write_text(json.dumps({'url': url, 'token': server.app_token}), encoding='utf-8')
        temporary.replace(statefile)
        history.start()
        radar.start()
        lobbies.start()
        if not no_tray:
            try:
                import pystray
                from PIL import Image, ImageDraw
                image = Image.new('RGB', (64, 64), '#25251f')
                draw = ImageDraw.Draw(image)
                # A compact, code-drawn fortress / M mark, legible at 16 px.
                draw.rectangle((12, 13, 22, 51), fill='#efeee7')
                draw.rectangle((42, 13, 52, 51), fill='#efeee7')
                draw.polygon([(22, 18), (32, 30), (42, 18), (42, 32), (32, 44), (22, 32)], fill='#cb4b2b')
                def open_window(*_):
                    if use_browser or not app_window.launch(url, directory / 'window-profile'):
                        webbrowser.open(url)
                def toggle(*_):
                    history.configure(not history.config['enabled'], history.config['folders'])
                def quit_app(*_):
                    threading.Thread(target=server.shutdown, daemon=True).start()
                menu = pystray.Menu(
                    pystray.MenuItem('Открыть Matchroom', open_window, default=True),
                    pystray.MenuItem('Автоподхват реплеев', toggle, checked=lambda _: history.config['enabled']),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem('Выйти', quit_app))
                icon = pystray.Icon('forts-matchroom', image, 'Forts Matchroom', menu)
            except ImportError:
                logging.info('Tray dependencies absent; browser mode remains available.')
        def serve():
            try:
                server.serve_forever(poll_interval=.2)
            finally:
                if icon:
                    icon.stop()
        worker = threading.Thread(target=serve, name='matchroom-http', daemon=True)
        worker.start()
        if sys.stdout:
            print(('READY ' if shell else '') + url, flush=True)
        if shell and parent_pid:
            threading.Thread(target=watch_parent, args=(parent_pid, server), name='matchroom-parent', daemon=True).start()
        if shell:
            threading.Thread(target=watch_stdin, args=(server,), name='matchroom-stdin', daemon=True).start()
        if window_mode:
            window = app_window.launch(url, directory / 'window-profile', app_window.load_placement(directory))
            if window:
                app_window.watch(window, server)
        if not no_browser and not window and not shell:
            webbrowser.open(url)
        try:
            if icon:
                icon.run()
                server.shutdown()
            else:
                while worker.is_alive():
                    worker.join(.5)
        except KeyboardInterrupt:
            server.shutdown()
        worker.join(timeout=10)
        return 0
    finally:
        app_window.close(window)
        notifier.close()
        from steam_service import native
        native.close()
        lobbies.close()
        radar.close()
        history.close()
        if server:
            server.server_close()
        statefile.unlink(missing_ok=True)
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--demo':
        from demo import main as demo_main
        del sys.argv[1]
        demo_main()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == '--steam-service':
        from steam_native import serve
        serve()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == '--steam-radar-worker':
        from steam_native import radar
        try:
            result = radar(int(sys.argv[3]), sys.argv[4:])
        except Exception as exc:
            result = {'status': 'unavailable', 'message': str(exc)}
        Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == '--steam-activity-worker':
        from steam_native import activity
        try:
            result = activity()
        except Exception as exc:
            result = {'status': 'unavailable', 'message': str(exc)}
        Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == '--steam-worker':
        from steam_native import lookup
        try:
            arguments = sys.argv[4:]
            leader_id = None
            if '--leader-id' in arguments:
                index = arguments.index('--leader-id')
                if index + 1 >= len(arguments):
                    raise ValueError('Не указан Steam ID для поиска соседнего места.')
                leader_id = arguments[index + 1]
                del arguments[index:index + 2]
            result = lookup(int(sys.argv[3]), arguments, leader_id)
        except Exception as exc:
            result = {'status': 'error', 'message': str(exc), 'players': {}}
        Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
        return 0
    parser = argparse.ArgumentParser(description='Forts Matchroom')
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true', help='Do not open any window (tests, headless).')
    parser.add_argument('--browser', action='store_true', help='Open in the default browser instead of an own window.')
    parser.add_argument('--no-tray', action='store_true')
    parser.add_argument('--shell', action='store_true', help='Run as the service of the native shell window.')
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--installer-path')
    parser.add_argument('--app-version', default=APP_VERSION)
    args = parser.parse_args()
    return run(args.files, data_dir=args.data_dir, no_browser=args.no_browser,
               no_tray=args.no_tray, installer_path=args.installer_path,
               app_version=args.app_version, use_browser=args.browser, shell=args.shell, parent_pid=args.parent_pid)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception('Matchroom failed')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'Matchroom Next', 0x10)
        else:
            raise
