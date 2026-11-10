"""Read-only checks shared by replay watchers and chronological lists."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import re

QUIET_SECONDS = 2
SCAN_INTERVAL = 2
STAMP = re.compile(r'(20\d{6}_\d{6})(?:\D|$)')

if os.name == 'nt':
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL


def filename_epoch(value: str) -> float | None:
    stamp = STAMP.search(value or '')
    if stamp:
        try:
            return datetime.strptime(stamp[1], '%Y%m%d_%H%M%S').timestamp()
        except ValueError:
            pass
    return None


def recorded_start(path: Path, stat=None, filename=None) -> float | None:
    """Trust creation time only for a replay that was actually written live."""
    named = filename_epoch(filename or path.name)
    if named is None:
        return None
    try:
        stat = stat or path.stat()
        duration = stat.st_mtime - stat.st_ctime
        if 10 <= duration <= 6 * 60 * 60 and abs(stat.st_ctime - named) <= 6 * 60 * 60:
            return stat.st_ctime
    except OSError:
        pass
    return None


def file_played_at_ns(path: Path, stat=None) -> int:
    stat = stat or path.stat()
    if recorded_start(path, stat) is not None:
        return stat.st_mtime_ns
    # A copy/import must keep its match date rather than its import date.
    named = filename_epoch(path.name)
    return int(named * 1_000_000_000) if named is not None else stat.st_mtime_ns


def _closed_to_writers(path: Path) -> bool:
    if os.name != 'nt':
        return True
    # Omitting FILE_SHARE_WRITE rejects an existing writer even during a
    # long pause between writes. Release the probe before running FWR.
    handle = _kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    _kernel.CloseHandle(handle)
    return True


def ready_for_analysis(path: Path, stat, now: float) -> bool:
    return stat.st_size >= 17 and now - stat.st_mtime >= QUIET_SECONDS and _closed_to_writers(path)


def unchanged_replay(path: Path, before) -> bool:
    try:
        after = path.stat()
        return ((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
                and _closed_to_writers(path))
    except OSError:
        return False
