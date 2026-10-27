"""Own application window for Matchroom Next.

Opens the local client in Microsoft Edge (or Chrome) app mode: a separate
window without tabs or an address bar. A dedicated browser profile keeps the
window independent from the user's normal browser, so closing it ends the
browser process and lets Next shut down with it.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

BROWSERS = ('msedge.exe', 'chrome.exe')
FALLBACK_PATHS = (
    ('ProgramFiles(x86)', r'Microsoft\Edge\Application\msedge.exe'),
    ('ProgramFiles', r'Microsoft\Edge\Application\msedge.exe'),
    ('LOCALAPPDATA', r'Microsoft\Edge\Application\msedge.exe'),
    ('ProgramFiles', r'Google\Chrome\Application\chrome.exe'),
    ('ProgramFiles(x86)', r'Google\Chrome\Application\chrome.exe'),
    ('LOCALAPPDATA', r'Google\Chrome\Application\chrome.exe'),
)
DEFAULT_SIZE = (1360, 860)
# A launch that exits this quickly handed its window to an already running
# browser process; Next then falls back to watching client requests.
HANDOFF_SECONDS = 8
IDLE_SHUTDOWN_SECONDS = 180


def find_app_browser() -> Path | None:
    if os.name != 'nt':
        return None
    try:
        import winreg
        for name in BROWSERS:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, rf'Software\Microsoft\Windows\CurrentVersion\App Paths\{name}') as key:
                        value, _ = winreg.QueryValueEx(key, '')
                    if value and Path(value).is_file():
                        return Path(value)
                except OSError:
                    continue
    except ImportError:
        pass
    for variable, relative in FALLBACK_PATHS:
        base = os.environ.get(variable)
        if base and (Path(base) / relative).is_file():
            return Path(base) / relative
    return None


def load_placement(directory: Path) -> dict | None:
    try:
        value = json.loads((directory / 'window.json').read_text(encoding='utf-8'))
        if all(isinstance(value.get(key), int) for key in ('x', 'y', 'width', 'height')):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def save_placement(directory: Path, rect: dict) -> None:
    path = directory / 'window.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(rect), encoding='utf-8')
    temporary.replace(path)


def launch(url: str, profile: Path, placement: dict | None = None) -> subprocess.Popen | None:
    """Start an app window; returns the browser process or None if no browser is available."""
    browser = find_app_browser()
    if not browser:
        return None
    profile.mkdir(parents=True, exist_ok=True)
    args = [str(browser), f'--app={url}', f'--user-data-dir={profile}',
            '--no-first-run', '--no-default-browser-check', '--disable-sync',
            '--disable-background-mode', '--disable-extensions', '--disable-features=Translate']
    if placement and placement.get('maximized'):
        args.append('--start-maximized')
    elif placement:
        args += [f"--window-size={placement['width']},{placement['height']}",
                 f"--window-position={placement['x']},{placement['y']}"]
    else:
        args.append('--window-size={},{}'.format(*DEFAULT_SIZE))
    try:
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        logging.exception('Could not start the app window')
        return None


def watch(process: subprocess.Popen, server, started: float | None = None) -> threading.Thread:
    """Shut the server down when the window closes."""
    started = started or time.monotonic()

    def run():
        process.wait()
        if time.monotonic() - started > HANDOFF_SECONDS:
            logging.info('App window closed; stopping Next.')
            server.shutdown()
            return
        logging.info('Browser handed the window over; watching client activity instead.')
        while True:
            time.sleep(10)
            seen = getattr(server, 'last_client_seen', None)
            if seen is not None and time.monotonic() - seen > IDLE_SHUTDOWN_SECONDS:
                logging.info('No client requests for %s s; stopping Next.', IDLE_SHUTDOWN_SECONDS)
                server.shutdown()
                return

    thread = threading.Thread(target=run, name='matchroom-window', daemon=True)
    thread.start()
    return thread


def focus(pid: int) -> bool:
    """Restore and raise the visible app window of a browser process; False if none."""
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes
    hwnd_t, visitor = wintypes.HWND, ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    for name, args, result in (
            ('EnumWindows', [visitor, wintypes.LPARAM], wintypes.BOOL),
            ('GetWindowThreadProcessId', [hwnd_t, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ('IsWindowVisible', [hwnd_t], wintypes.BOOL), ('IsIconic', [hwnd_t], wintypes.BOOL),
            ('GetWindow', [hwnd_t, wintypes.UINT], hwnd_t), ('GetWindowTextLengthW', [hwnd_t], ctypes.c_int),
            ('ShowWindow', [hwnd_t, ctypes.c_int], wintypes.BOOL),
            ('SetForegroundWindow', [hwnd_t], wintypes.BOOL), ('BringWindowToTop', [hwnd_t], wintypes.BOOL),
            ('GetForegroundWindow', [], hwnd_t),
            ('AttachThreadInput', [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL)):
        function = getattr(user32, name)
        function.argtypes, function.restype = args, result
    found = []

    @visitor
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if (owner.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4)
                and user32.GetWindowTextLengthW(hwnd) > 0):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        # Without foreground rights Windows only flashes the taskbar button;
        # sharing input with the current foreground thread lifts that limit.
        kernel = ctypes.WinDLL('kernel32')
        current = kernel.GetCurrentThreadId()
        target = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        if target and target != current and user32.AttachThreadInput(current, target, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(current, target, False)
    return True


def close(process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
