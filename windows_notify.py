"""Windows toast notifications for radar alerts and new lobby rooms.

Toasts are shown by the native helper (``NextLobbyBridge.exe --toasts``) under
the Matchroom Next AppUserModelID registered in HKCU. Nothing is sent while the
Next window has focus, each kind is rate limited and toasts are silent, so a
match is never interrupted by a stream of popups. A click opens Next on the
matching view.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from xml.sax.saxutils import escape, quoteattr

APP_ID = 'FortsMatchroomNext'
APP_NAME = 'Matchroom Next'
HELPER = Path(__file__).with_name('lobby_native') / 'NextLobbyBridge.exe'
ICON = Path(__file__).with_name('forts_icon.png')
ROUTES = ('radar', 'lobbies')
COOLDOWN_SECONDS = {'radar': 120, 'lobbies': 180}
LIFETIME_MINUTES = {'radar': 10, 'lobbies': 5, 'test': 2}
# A focused client polls every 1.5 s; silence means the window is gone.
FOCUS_STALE_SECONDS = 10
BLOCKED = ('DisabledForApplication', 'DisabledForUser', 'DisabledByGroupPolicy', 'DisabledByManifest')

TEXTS = {
    'ru': {
        'queue': ('Сильные игроки ищут рейтинг', 'Сейчас в поиске: {names}.'),
        'recent': ('Сильные игроки в рейтинге', 'Очки только что сдвинулись: {names}.'),
        'room': 'Новая комната',
        'rooms': 'Новые комнаты',
        'more': '{names} и ещё {count}',
        'test': ('Уведомления включены', 'Так Next подскажет про сильных игроков и новые комнаты.'),
        'tiers': {'fpl': 'FPL', 'top20': 'Топ-20', 'top80': 'Места 21–80', 'current': 'Текущий топ'},
    },
    'en': {
        'queue': ('Strong players in ranked queue', 'Searching now: {names}.'),
        'recent': ('Strong players are playing ranked', 'Score just moved: {names}.'),
        'room': 'New room',
        'rooms': 'New rooms',
        'more': '{names} and {count} more',
        'test': ('Notifications are on', 'This is how Next flags strong players and new rooms.'),
        'tiers': {'fpl': 'FPL', 'top20': 'Top 20', 'top80': 'Places 21–80', 'current': 'Current top'},
    },
}


def toast_xml(title, body, route, attribution=None):
    """Silent ToastGeneric markup; every text, including room names, is escaped."""
    texts = '<text>{}</text><text>{}</text>'.format(escape(title), escape(body))
    if attribution:
        texts += '<text placement="attribution">{}</text>'.format(escape(attribution))
    return ('<toast launch={}><visual><binding template="ToastGeneric">{}</binding></visual>'
            '<audio silent="true"/></toast>').format(quoteattr(route), texts)


def _names(values, limit=3):
    names = [value.strip()[:60] for value in values if isinstance(value, str) and value.strip()]
    return ', '.join(names[:limit]), len(names)


class WindowsNotifier:
    def __init__(self, directory=None, enabled=lambda: False, language=lambda: 'ru', opener=None,
                 transport=None, clock=time.monotonic):
        self.directory = Path(directory) if directory else None
        self.enabled = enabled
        self.language = language
        self.opener = opener
        self.transport = transport
        self.clock = clock
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.process = None
        self.responses = None
        self.focused = False
        self.focus_reported = None
        self.sent = {}
        self.setting = None
        self.error = ''
        self.open_seq = 0
        self.open_route = None

    def available(self):
        return bool(self.transport) or (os.name == 'nt' and HELPER.is_file())

    def client_focus(self, focused):
        with self.lock:
            self.focused = bool(focused)
            self.focus_reported = self.clock()

    def _texts(self):
        return TEXTS['en' if self.language() == 'en' else 'ru']

    def _allow(self, kind):
        if not self.enabled() or not self.available():
            return False
        with self.lock:
            now = self.clock()
            if self.focused and self.focus_reported is not None and now - self.focus_reported <= FOCUS_STALE_SECONDS:
                return False
            last = self.sent.get(kind)
            if last is not None and now - last < COOLDOWN_SECONDS[kind]:
                return False
            self.sent[kind] = now
            return True

    def radar_alert(self, notification):
        """Strong players confirmed in queue or with a fresh score change."""
        evidence = notification.get('evidence')
        names, _count = _names(notification.get('names', []))
        if evidence not in ('queue', 'recent') or not names or not self._allow('radar'):
            return False
        text = self._texts()
        title, body = text[evidence]
        self._background('radar', title, body.format(names=names), 'radar', text['tiers'].get(notification.get('tier')))
        return True

    def new_rooms(self, names):
        shown, count = _names(names)
        if not shown or not self._allow('lobbies'):
            return False
        text = self._texts()
        body = text['more'].format(names=shown, count=count - 3) if count > 3 else shown
        self._background('lobbies', text['room' if count == 1 else 'rooms'], body, 'lobbies')
        return True

    def test(self):
        """An explicit check from Settings: ignores focus and rate limits."""
        if not self.available():
            raise ValueError('Уведомления Windows недоступны в этой версии.')
        title, body = self._texts()['test']
        if not self._show('test', title, body, 'radar'):
            raise ValueError(self.error or 'Windows не показал уведомление.')
        return self.snapshot()

    def _background(self, *args):
        threading.Thread(target=self._show, args=args, daemon=True, name='toast-send').start()

    def _show(self, tag, title, body, route, attribution=None):
        message = {'command': 'show', 'tag': tag, 'minutes': LIFETIME_MINUTES[tag],
                   'xml': toast_xml(title, body, route, attribution)}
        try:
            with self.io_lock:
                reply = self.transport(message) if self.transport else self._request(message)
        except (OSError, RuntimeError, ValueError) as exc:
            reply = {'error': str(exc)}
        with self.lock:
            self.setting = reply.get('setting', self.setting)
            self.error = reply.get('error') or ''
        if self.error:
            logging.warning('Windows notification failed: %s', self.error)
        return not self.error

    def _register(self):
        """Name and icon for the toast header; Windows refuses toasts from unknown app IDs."""
        import winreg
        icon = ICON
        if self.directory:
            icon = self.directory / 'notification-icon.png'
            if not icon.is_file():
                shutil.copyfile(ICON, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\AppUserModelId\{APP_ID}') as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon.resolve()))

    def _request(self, message):
        if self.process is None or self.process.poll() is not None:
            self._start()
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()
        try:
            return self.responses.get(timeout=10)
        except queue.Empty:
            raise RuntimeError('Помощник уведомлений не отвечает.')

    def _start(self):
        self._register()
        self.process = subprocess.Popen(
            [str(HELPER), '--toasts', APP_ID, str(os.getpid())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process, responses = self.process, queue.Queue()
        self.responses = responses

        def receive():
            try:
                for line in process.stdout:
                    try:
                        message = json.loads(line.lstrip('﻿'))
                    except ValueError:
                        continue
                    if 'activated' in message:
                        self.activated(message['activated'])
                    elif 'failed' in message:
                        logging.warning('Windows dropped the %s notification', message['failed'])
                    else:
                        responses.put(message)
            finally:
                responses.put({'error': 'Помощник уведомлений завершился.'})
        threading.Thread(target=receive, daemon=True, name='toast-events').start()

    def activated(self, route):
        """A toast click: the client navigates on its next poll, the opener raises the window."""
        if route not in ROUTES:
            return
        with self.lock:
            self.open_seq += 1
            self.open_route = route
        if self.opener:
            try:
                self.opener(route)
            except Exception:
                logging.exception('Could not open Next from a notification')

    def snapshot(self):
        with self.lock:
            return {'available': self.available(), 'enabled': bool(self.enabled()),
                    'blocked': self.setting in BLOCKED, 'error': self.error,
                    'open': {'seq': self.open_seq, 'route': self.open_route}}

    def close(self):
        with self.io_lock:
            process, self.process = self.process, None
            if not process:
                return
            if process.poll() is None:
                try:
                    process.stdin.write('{"command":"quit"}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
