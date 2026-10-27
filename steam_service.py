"""Single gateway to Steam for the whole app.

* NativeService keeps ONE long-lived Steamworks helper process and sends it
  requests one at a time. Before, every request started a new process that
  initialised and shut Steam down again, often several at once.
* Gate limits HTTP requests to steamcommunity.com / api.steampowered.com:
  a minimum gap between requests and an exponential pause after Steam
  answers 429/403/503, so Steam never sees a burst.
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import tempfile
import threading
import time
from itertools import count
from pathlib import Path

from game_process import forts_running

NATIVE_GAP_SECONDS = 1.0
NATIVE_FAILURES_BEFORE_PAUSE = 3
NATIVE_PAUSE_SECONDS = 90


class Gate:
    """Spaces out requests to one Steam host and backs off after refusals."""

    def __init__(self, min_gap=1.5, max_wait=12.0, base_pause=60.0, max_pause=1800.0):
        self.min_gap, self.max_wait = min_gap, max_wait
        self.base_pause, self.max_pause = base_pause, max_pause
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.blocked_until = 0.0
        self.failures = 0

    def wait_turn(self) -> bool:
        """Block until a request may go out; False when paused or the queue is too deep."""
        with self.lock:
            now = time.monotonic()
            if now < self.blocked_until:
                return False
            slot = max(now, self.next_slot)
            if slot - now > self.max_wait:
                return False
            self.next_slot = slot + self.min_gap
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return True

    def succeeded(self) -> None:
        with self.lock:
            self.failures = 0

    def refused(self, retry_after: float | None = None) -> float:
        """Steam said no: pause all requests. Returns the pause in seconds."""
        with self.lock:
            if time.monotonic() < self.blocked_until:
                # Requests already in flight when the first refusal came back: one event, one pause.
                return self.blocked_until - time.monotonic()
            self.failures += 1
            pause = retry_after if retry_after else self.base_pause * 2 ** (self.failures - 1)
            pause = min(self.max_pause, max(5.0, float(pause)))
            self.blocked_until = max(self.blocked_until, time.monotonic() + pause)
            logging.warning('Steam refused requests (%s in a row); pausing %.0f s.', self.failures, pause)
            return pause

    def blocked_for(self) -> float:
        return max(0.0, self.blocked_until - time.monotonic())


# One gate for steamcommunity.com profile pages (the strictest limit), one for the API host.
community = Gate(min_gap=1.5, base_pause=60)
webapi = Gate(min_gap=2.0, base_pause=120)


class NativeService:
    """Long-lived Steamworks helper; calls are serialised and spaced out."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.replies: queue.Queue = queue.Queue()
        self.directory: tempfile.TemporaryDirectory | None = None
        self.ids = count(1)
        self.last_call = 0.0
        self.failures = 0
        self.paused_until = 0.0

    # -- process management -------------------------------------------------
    def _start(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix='forts-steam-')
        Path(self.directory.name, 'steam_appid.txt').write_text('410900', encoding='ascii')
        if getattr(sys, 'frozen', False):
            command = [sys.executable, '--steam-service']
        else:
            command = [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('steam_native.py')), '--service']
        self.replies = queue.Queue()
        self.process = subprocess.Popen(
            command, cwd=self.directory.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        replies, stream = self.replies, self.process.stdout

        def read():
            for line in stream:
                if line.startswith('{'):
                    try:
                        replies.put(json.loads(line))
                    except ValueError:
                        pass
            replies.put(None)

        threading.Thread(target=read, name='steam-service-reader', daemon=True).start()
        logging.info('Steam helper started (pid %s).', self.process.pid)

    def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        if process:
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.directory:
            try:
                self.directory.cleanup()
            except OSError:
                pass
            self.directory = None

    def close(self) -> None:
        with self.lock:
            self.stop()

    # -- requests -----------------------------------------------------------
    def call(self, op: str, args: list, timeout: float = 25.0) -> dict:
        """Run one operation. Never raises; returns a status dict."""
        with self.lock:
            now = time.monotonic()
            if now < self.paused_until:
                return {'status': 'unavailable', 'message': 'Steam временно ограничил запросы, ждём.',
                        'retry_in': int(self.paused_until - now), 'players': {}}
            # The helper only lives while Forts runs, as a short-lived worker did.
            if not forts_running():
                self.stop()
                return {'status': 'paused', 'game_running': False}
            gap = NATIVE_GAP_SECONDS - (now - self.last_call)
            if gap > 0:
                time.sleep(gap)
            try:
                if not self.process or self.process.poll() is not None:
                    self.stop()
                    self._start()
                request_id = next(self.ids)
                self.process.stdin.write(json.dumps({'id': request_id, 'op': op, 'args': args}, ensure_ascii=True) + '\n')
                self.process.stdin.flush()
                deadline = time.monotonic() + timeout
                while True:
                    reply = self.replies.get(timeout=max(0.1, deadline - time.monotonic()))
                    if reply is None:
                        raise RuntimeError('Steam helper exited')
                    if reply.get('id') == request_id:
                        result = reply['result']
                        break
                self.last_call = time.monotonic()
                if result.get('status') in ('unavailable', 'error'):
                    self._failed()
                else:
                    self.failures = 0
                return result
            except (queue.Empty, OSError, RuntimeError, ValueError) as exc:
                logging.warning('Steam helper call %s failed: %s', op, exc)
                self.stop()
                self._failed()
                return {'status': 'error', 'message': 'Не удалось прочитать данные Steam.', 'players': {}}

    def _failed(self) -> None:
        self.failures += 1
        if self.failures >= NATIVE_FAILURES_BEFORE_PAUSE:
            self.paused_until = time.monotonic() + NATIVE_PAUSE_SECONDS * min(8, 2 ** (self.failures - NATIVE_FAILURES_BEFORE_PAUSE))
            self.stop()
            logging.warning('Steam helper failed %s times; pausing until %.0f s from now.',
                            self.failures, self.paused_until - time.monotonic())

    def blocked_for(self) -> float:
        return max(0.0, self.paused_until - time.monotonic())


native = NativeService()


def throttle_state() -> dict:
    """What the UI shows when Steam is holding requests back."""
    values = {'community': community.blocked_for(), 'webapi': webapi.blocked_for(), 'native': native.blocked_for()}
    longest = max(values.values())
    return {'limited': longest > 0, 'seconds': int(longest)}
