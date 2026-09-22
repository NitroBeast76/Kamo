"""Autostart registration for Kamo.

Writes / removes a registry value under
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

Uses the path of the currently running interpreter or exe, which
resolves correctly both when running from source and from the
compiled Kamo.exe.

Why HKCU and not HKLM:
    - no admin rights needed
    - per-user by design
    - uninstalling means deleting one value

Why not the Startup folder:
    - the registry value is one line, a .lnk file is more moving parts
    - the user can still see and disable Kamo via Task Manager's
      Startup tab, which surfaces registry Run entries

Public API:
    is_enabled() -> bool
    enable()     -> bool
    disable()    -> bool
    refresh()    -> bool   # re-register with the current path
"""

from __future__ import annotations

import sys
from pathlib import Path

import winreg


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Kamo"


def _launch_command() -> str:
    """The command Windows will run at login.

    Frozen (compiled exe):
        "C:\\path\\to\\Kamo.exe"

    From source:
        "C:\\path\\to\\pythonw.exe" "C:\\path\\to\\kamo\\__main__.py"

    pythonw (not python) so no console window flashes at login.
    Paths are quoted because both may contain spaces.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = exe  # fall back to python.exe if pythonw is missing

    entry = Path(__file__).parent / "__main__.py"
    return f'"{pythonw}" "{entry}"'


def is_enabled() -> bool:
    """Return True if Kamo is registered to run at login."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> bool:
    """Register Kamo to run at login. Returns True on success."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command()
            )
        return True
    except OSError:
        return False


def disable() -> bool:
    """Remove Kamo's login entry. Returns True on success (or if
    there was nothing to remove)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True  # already gone; treat as success
    except OSError:
        return False


def refresh() -> bool:
    """Re-register with the current path.

    Useful after moving the exe or switching between source and
    compiled builds. No-op if Kamo isn't currently registered.
    """
    if not is_enabled():
        return False
    return enable()