"""Adapter base class and shared helpers.

An adapter is the only place in Kamo that knows how to talk to a
specific app. It reads a config slice, resolves paths, and writes
whatever format that app expects. The engine never sees this; it just
calls is_available() and apply(theme).

Every adapter is constructed once at startup and lives for the whole
process. Adapters must be safe to call from any thread: the engine
calls them from the settle-timer's worker thread.

Contract for subclasses:

    name            class attribute, matches the config section
    is_available()  -> bool
    apply(theme)    -> None
    reload()        -> None (optional, from base class)

apply() should not raise on the "file not found" or "config empty"
paths; that is what is_available() is for. apply() may raise on
genuinely unexpected errors (disk full, permission denied), and the
engine will log them per-adapter without stopping the others.

reload() kills and relaunches the target app so config changes take
effect without the user manually restarting it. Adapters opt in by
setting `process_name` and `launch_command` as class attributes.

Helpers in this file:

    cfg_value()      - read a config value with fallback
    cfg_path()       - read a path with ~ and %ENV% expansion
    cfg_dict()       - read a dict, merged over a default
    resolve_path()   - expand ~ and %ENV% in a config path
    first_existing() - try a list of candidate paths
    read_text()      - utf-8-sig safe read
    write_text()     - atomic write with temp + replace
    is_process_running() - psutil check by image name
    kill_process()       - best-effort kill by image name
    launch_detached()    - spawn without window or Kamo parentage
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from .. import log
from ..theme import Theme


class Adapter:
    """Base class. Subclasses fill in `name`, `is_available`, `apply`."""

    name: str = "unnamed"

    # Opt-in reload support. Set both in a subclass to enable the
    # base-class reload() method. If either is None, reload() is a
    # no-op and the adapter must tell the user to restart manually.
    process_name: str | None = None
    launch_command: list[str] | None = None
    restart_delay: float = 0.8

    def __init__(self, cfg: dict):
        """`cfg` is the merged config slice for this adapter, from
        config.get_adapter(). It already includes `enabled` and any
        user overrides. Adapters read from `self.cfg` in apply()."""
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if this adapter can do anything on this machine.

        Typically checks that the target config file exists. Must not
        raise; return False on any error.
        """
        return False

    def apply(self, theme: Theme) -> None:
        """Write `theme` into the target app's config.

        Called by the engine for every wallpaper change. Should be
        idempotent: applying the same Theme twice must produce the
        same file contents. Should log its own progress and warnings.
        """
        raise NotImplementedError

    def reload(self) -> None:
        """Kill and relaunch the target app if it's running.

        No-op if `process_name` isn't set. No-op if the process isn't
        running (config applies next time the user launches it). No-op
        if `launch_command` isn't set but logs a warning so the user
        knows to restart manually.

        Called by subclasses from their apply() after writing config,
        typically guarded by a `restart` config flag.
        """
        if not self.process_name:
            return

        if not is_process_running(self.process_name):
            self.log_info(
                f"{self.process_name} not running; "
                "config applies on next launch"
            )
            return

        if not self.launch_command:
            self.log_warn(
                f"{self.process_name} running but no launch_command set; "
                "restart manually"
            )
            return

        if not kill_process(self.process_name):
            self.log_warn(f"could not kill {self.process_name}")
            return

        time.sleep(self.restart_delay)

        if launch_detached(self.launch_command):
            self.log_info(f"restarted {self.process_name}")
        else:
            self.log_warn(
                f"could not relaunch {self.process_name}; "
                f"launch manually with {self.launch_command!r}"
            )

    # ------------------------------------------------------------------
    # Utilities for subclasses
    # ------------------------------------------------------------------

    def cfg_value(self, key: str, default):
        """Read self.cfg[key], falling back to `default`."""
        return self.cfg.get(key, default)

    def cfg_path(self, key: str, default: str | Path | None) -> Path | None:
        """Read a path from config, expanding ~ and %ENV%.

        Returns None if neither the config nor the default resolves.
        """
        raw = self.cfg.get(key, default)
        if raw is None:
            return None
        return resolve_path(raw)

    def cfg_dict(self, key: str, default: dict) -> dict:
        """Read a dict from config, merged over `default`.

        The merge is shallow. For role maps like
        {"background": "base", "text": "text"}, that's sufficient:
        users override individual keys, not nested dicts.
        """
        user = self.cfg.get(key)
        if not isinstance(user, dict):
            return dict(default)
        merged = dict(default)
        merged.update(user)
        return merged

    def log_info(self, msg: str) -> None:
        log.info(f"[{self.name}] {msg}")

    def log_warn(self, msg: str) -> None:
        log.warn(f"[{self.name}] {msg}")

    def log_error(self, msg: str) -> None:
        log.error(f"[{self.name}] {msg}")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name} enabled={self.enabled}>"


# ---------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------

def resolve_path(raw: str | Path) -> Path:
    """Expand ~ and %VAR% / $VAR, then return an absolute Path.

    Order of expansion:
        1. os.path.expandvars  - %APPDATA% on Windows, $HOME on POSIX
        2. os.path.expanduser  - ~ and ~user
    Then make absolute against the current working directory.
    """
    s = str(raw)
    s = os.path.expandvars(s)
    s = os.path.expanduser(s)
    return Path(s).resolve()


def first_existing(candidates: list[str | Path]) -> Path | None:
    """Return the first candidate that exists on disk, or None."""
    for c in candidates:
        p = resolve_path(c)
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------

def read_text(path: Path) -> str:
    """Read a text file with utf-8 and a BOM-tolerant fallback.

    PowerShell, editors on Windows, and .NET tools sometimes emit
    utf-8 with a BOM. `encoding="utf-8-sig"` strips it if present and
    behaves identically to plain utf-8 if not.
    """
    return path.read_text(encoding="utf-8-sig")


def write_text(path: Path, content: str) -> None:
    """Write text atomically: temp file in the same directory, then
    os.replace. Prevents a half-written config from being observed by
    the target app's file watcher.
    """
    tmp = path.with_suffix(path.suffix + ".kamo-tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_text_bytes(path: Path, content: bytes) -> None:
    """Same as write_text, but for bytes (e.g. when the target format
    is picky about newline handling and we're doing a byte-for-byte
    rewrite).
    """
    tmp = path.with_suffix(path.suffix + ".kamo-tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


# ---------------------------------------------------------------------
# Process helpers (used by Adapter.reload and by adapters that manage
# their own process lifetime, like chronoterm)
# ---------------------------------------------------------------------

def is_process_running(name: str) -> bool:
    """Check if any process with this image name is running.

    `name` is the executable name, e.g. "yasb.exe". Compared
    case-insensitively.
    """
    if psutil is None:
        return False
    try:
        target = name.lower()
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == target:
                return True
        return False
    except Exception:
        return False


def kill_process(name: str, timeout: float = 5.0) -> bool:
    """Kill every process matching the image name. Returns True if at
    least one was killed. Errors are swallowed; this is best-effort.
    """
    if psutil is None:
        return False
    target = name.lower()
    killed = False
    try:
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() != target:
                continue
            try:
                p.kill()
                p.wait(timeout=timeout)
                killed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass
    except Exception:
        pass
    return killed


def launch_detached(cmd: list[str]) -> bool:
    """Launch a process detached from Kamo. Returns True on success.

    Uses DETACHED_PROCESS | CREATE_NO_WINDOW on Windows so the child
    survives Kamo exiting and doesn't flash a console window.
    """
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except (OSError, FileNotFoundError):
        return False