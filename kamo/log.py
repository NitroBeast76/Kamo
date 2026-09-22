"""Logging for Kamo.

A tray app built with --noconsole has no visible stdout, so the log
file is the source of truth. Every line goes both to stderr (useful
when running from a terminal) and to ~/.config/kamo/kamo.log.

Rotation: when the log exceeds MAX_BYTES, it is renamed to kamo.log.1
and a fresh file is started. One generation of history, no more. Enough
to see "what happened last run" without a log-shipping setup.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path


LOG_DIR = Path.home() / ".config" / "kamo"
LOG_PATH = LOG_DIR / "kamo.log"
MAX_BYTES = 512 * 1024  # 512 KiB

_LEVELS = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
}

_level = _LEVELS["info"]
_lock = threading.Lock()
_initialized = False


def init(level: str = "info") -> None:
    """Set the active level and ensure the log directory exists.

    Safe to call more than once. Called by engine.py once config is
    loaded; before that, info-level logging still works with defaults.
    """
    global _level, _initialized
    _level = _LEVELS.get(level.lower(), _LEVELS["info"])
    with _lock:
        if not _initialized:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _initialized = True


def _rotate_if_needed() -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
            backup = LOG_PATH.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            LOG_PATH.rename(backup)
    except OSError:
        # If rotation fails, keep appending. Not worth crashing over.
        pass


def _write(level: str, msg: str) -> None:
    if _LEVELS[level] < _level:
        return

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {level.upper():5s} {msg}"

    with _lock:
        try:
            _rotate_if_needed()
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # never crash on logging

    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


def debug(msg: str) -> None:
    _write("debug", msg)


def info(msg: str) -> None:
    _write("info", msg)


def warn(msg: str) -> None:
    _write("warn", msg)


def error(msg: str) -> None:
    _write("error", msg)


def exception(msg: str) -> None:
    """Log an error with the current traceback appended."""
    import traceback
    tb = traceback.format_exc().rstrip()
    _write("error", f"{msg}\n{tb}")