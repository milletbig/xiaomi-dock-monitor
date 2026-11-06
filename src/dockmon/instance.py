"""Single-instance guard backed by a named Windows mutex.

The mutex lives for the lifetime of the process; when a second instance
starts it exits early and tries to raise the existing window instead.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

MUTEX_NAME = r"Local\XiaomiDockMonitor"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wt.HANDLE
_kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
_user32.FindWindowW.restype = wt.HWND

_handle: int | None = None


def acquire() -> bool:
    """Return ``True`` when this process is the only running instance."""
    global _handle
    if _handle:
        return True
    handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _handle = handle
    return True


def focus_existing(title: str) -> bool:
    """Restore and focus the window owned by the first instance."""
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False
    _user32.ShowWindow(hwnd, _SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    return True


def release() -> None:
    global _handle
    if _handle:
        _kernel32.CloseHandle(_handle)
        _handle = None
