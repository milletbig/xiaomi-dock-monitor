"""Per-user auto-start registration (``HKCU\\...\\CurrentVersion\\Run``).

The command keeps the monitor in tray mode so logging in does not pop the
main window.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "XiaomiDockMonitor"
TRAY_FLAG = "--tray"


def build_command(executable: str, frozen: bool) -> str:
    """Return the command line that is stored in the Run key."""
    if frozen:
        return f'"{executable}" {TRAY_FLAG}'
    return f'"{executable}" -m dockmon {TRAY_FLAG}'


def current_command() -> str:
    """Command for the interpreter that is running this process."""
    if getattr(sys, "frozen", False):
        return build_command(sys.executable, True)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = str(pythonw) if pythonw.exists() else sys.executable
    return build_command(executable, False)


def is_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Enable or disable auto-start, returning the resulting state."""
    if winreg is None:
        return False
    try:
        if enabled:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, current_command())
        else:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return is_enabled()
    return enabled


def toggle() -> bool:
    return set_enabled(not is_enabled())
