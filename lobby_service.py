"""Cached lobby facade; native reads run in isolated, session-guarded C# workers."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from game_process import forts_running
from steam_data import _native_lock

FORTS_APP_ID = '410900'
LAST_LOBBY_TTL = 6 * 3600


class LobbyService:
    def __init__(self, directory, transport=None):
        self.directory = Path(directory)
        self.settings_path = self.directory / 'lobby-settings.json'
        self.lock = threading.RLock()
        self.io_lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.process = None
        self.responses = queue.Queue()
        self.thread = None
        self.transport = transport
        self.pending_refresh = None
        self.busy = False
        self.notifier = None
        self.announced = None
        self.state = {'status': 'checking', 'lobbies': [], 'game_running': False, 'fresh': False}
        self.settings = {'notify': False, 'favorites': [], 'last_lobby': None}
        try:
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            self.settings['notify'] = saved.get('notify') is True
            self.settings['favorites'] = [sid for sid in saved.get('favorites', []) if self.valid_id(sid)][:500]
            self.settings['last_lobby'] = self._clean_last(saved.get('last_lobby'))
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def valid_id(value):
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 20:
            return False
        number = int(value)
        return (number >> 56 == 1 and (number >> 52) & 15 == 8 and
                (number >> 32) & 0x40000 != 0 and number & 0xffffffff != 0)

    @classmethod
    def _clean_last(cls, value):
        if not isinstance(value, dict) or not cls.valid_id(value.get('id')):
            return None
        at = value.get('at')
        if type(at) not in (int, float) or time.time() - at > LAST_LOBBY_TTL:
            return None

        def text(key):
            return str(value.get(key) or '')[:120]

        return {'id': value['id'], 'name': text('name'), 'map': text('map'), 'host': text('host'),
                'at': at, 'game_closed': value.get('game_closed') is True}

    def _save_settings(self, updated):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated), encoding='utf-8')
        temporary.replace(self.settings_path)
        self.settings = updated

    def _start_helper(self):
        helper = Path(__file__).with_name('lobby_native') / 'NextLobbyBridge.exe'
        if not helper.exists():
            raise RuntimeError('Помощник лобби не собран. Запусти build_exe.py.')
        self.process = subprocess.Popen(
            [str(helper), '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process = self.process
        def receive():
            try:
                for line in process.stdout:
                    try:
                        self.responses.put(json.loads(line.lstrip('﻿')))
                    except ValueError:
                        continue
            finally:
                self.responses.put({'error': 'Помощник лобби завершился.'})
        threading.Thread(target=receive, daemon=True, name='lobby-protocol').start()

    def _request(self, command, **body):
        with self.io_lock:
            if self.transport:
                return self.transport({'command': command, **body})
            if self.process is None:
                self._start_helper()
            if self.process.poll() is not None:
                raise RuntimeError('Помощник лобби остановлен. Перезапусти Next.')
            self.process.stdin.write(json.dumps({'command': command, **body}) + '\n')
            self.process.stdin.flush()
            try:
                result = self.responses.get(timeout=35)
            except queue.Empty:
                self.process.kill()
                self.process.wait(timeout=5)
                raise RuntimeError('Помощник лобби не отвечает. Перезапусти Next.')
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            state.update(busy=self.busy, notify=self.settings['notify'], favorites=list(self.settings['favorites']),
                         last_lobby=self._clean_last(self.settings['last_lobby']))
            return state

    def _publish(self, state):
        with self.lock:
            self.state = state
            last = self.settings['last_lobby']
            # The game going away after a join is what makes the room worth offering again.
            if last and state.get('game_running') is False and not last.get('game_closed'):
                try:
                    self._save_settings(dict(self.settings, last_lobby=dict(last, game_closed=True)))
                except OSError:
                    logging.exception('Could not mark the last lobby')
        self._announce(state)

    def _announce(self, state):
        # The helper counts new rooms per process; a restart starts from zero again.
        sequence = state.get('notification_sequence')
        if type(sequence) is not int:
            return
        previous, self.announced = self.announced, sequence
        if (self.notifier and previous is not None and sequence > previous
                and state.get('fresh') and state.get('new_names')):
            self.notifier.new_rooms(state['new_names'])

    def refresh(self, once=False):
        if not isinstance(once, bool):
            raise ValueError('Некорректный режим запроса.')
        with self.lock:
            if self.busy or self.pending_refresh is not None:
                return self.snapshot()
            # A closed-game read can only be scheduled by this explicit endpoint.
            if self.state.get('game_running') is False and not once:
                return self.snapshot()
            self.pending_refresh = 'once' if once else 'read'
            self.busy = True
        self.wake.set()
        return self.snapshot()

    def configure(self, notify=None, favorite=None, forget_last=False):
        with self.lock:
            updated = copy.deepcopy(self.settings)
            if notify is not None:
                if not isinstance(notify, bool):
                    raise ValueError('Некорректная настройка уведомлений.')
                updated['notify'] = notify
            if favorite is not None:
                if not self.valid_id(favorite):
                    raise ValueError('Некорректный ID лобби.')
                if favorite in updated['favorites']:
                    updated['favorites'].remove(favorite)
                else:
                    updated['favorites'] = (updated['favorites'] + [favorite])[-500:]
            if forget_last is True:
                updated['last_lobby'] = None
            self._save_settings(updated)
        self.wake.set()
        return self.snapshot()

    def join(self, lobby_id):
        if not self.valid_id(lobby_id):
            raise ValueError('Некорректный ID лобби.')
        # The controller rechecks its own snapshot, Steam and exact game session;
        # no client-supplied URL or host is ever passed to Process.Start.
        result = self._request('join', id=lobby_id)
        if not result.get('ok'):
            raise ValueError(result.get('message') or 'Вход недоступен.')
        self._remember_lobby(lobby_id)
        return result

    def _remember_lobby(self, lobby_id):
        with self.lock:
            row = next((r for r in self.state.get('lobbies', []) if r.get('id') == lobby_id), {})
            updated = copy.deepcopy(self.settings)
            updated['last_lobby'] = self._clean_last({
                'id': lobby_id, 'name': row.get('name'), 'map': row.get('map'), 'host': row.get('host'),
                'at': time.time()})
            try:
                self._save_settings(updated)
            except OSError:
                logging.exception('Could not remember the last lobby')

    def restart_game(self):
        """Close Forts if it is running (also when hung) and start it through Steam."""
        if os.name != 'nt':
            raise RuntimeError('Перезапуск доступен только в Windows.')
        if forts_running(refresh=True):
            subprocess.run(['taskkill', '/F', '/IM', 'Forts.exe'], capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
            deadline = time.monotonic() + 10
            while forts_running(refresh=True):
                if time.monotonic() > deadline:
                    raise RuntimeError('Не удалось закрыть Forts.')
                time.sleep(.3)
            # Let Steam notice the exit before asking it to start the game again.
            time.sleep(1.5)
        try:
            os.startfile('steam://rungameid/' + FORTS_APP_ID)
        except OSError:
            raise RuntimeError('Не удалось запустить Forts через Steam.')
        self.wake.set()
        return {'ok': True}

    def sound(self):
        return self._request('sound')

    def start(self):
        def monitor():
            due = 0
            last_session = None
            sent_notify = None
            while not self.stop.is_set():
                try:
                    # Detection serves both the chime and Windows toasts; only the chime is optional here.
                    sound = self.settings['notify']
                    wanted = (sound or bool(self.notifier and self.notifier.enabled()), sound)
                    if sent_notify != wanted:
                        self._request('notify', enabled=wanted[0], sound=sound)
                        sent_notify = wanted
                    state = self._request('status')
                    session = state.get('session')
                    if session != last_session:
                        due = 0
                        last_session = session
                    self._publish(state)
                    with self.lock:
                        command = self.pending_refresh
                        self.pending_refresh = None
                    if command or (state.get('game_running') and time.monotonic() >= due):
                        with self.lock:
                            self.busy = True
                        # Ranks/activity use the same lock, so independent Steam
                        # workers cannot initialize concurrently.
                        with _native_lock:
                            if self.stop.is_set():
                                break
                            self._publish(self._request(command or 'read'))
                        due = time.monotonic() + 15
                except Exception as exc:
                    logging.exception('Lobby monitoring failed')
                    with self.lock:
                        self.state.update(status='error', message=str(exc), fresh=False)
                        for row in self.state.get('lobbies', []):
                            row['can_join'] = False
                finally:
                    with self.lock:
                        self.busy = False
                self.wake.wait(1)
                self.wake.clear()
        self.thread = threading.Thread(target=monitor, daemon=True, name='lobby-monitor')
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=40)
        with self.io_lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.stdin.write('{"command":"quit"}\n')
                    self.process.stdin.flush()
                    self.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.process:
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        stream.close()
