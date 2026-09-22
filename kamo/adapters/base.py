"""Adapter base class and shared helpers.

An adapter is the only place in Kamo that knows how to talk to a
specific app. It reads a config slice, resolves paths, and writes
whatever format that app expects. The engine never sees this; it just
calls is_available() and apply(theme).

Every adapter is constructed once at startup and lives for the whole
process. Adapters must be safe to call from any thread: the engine
calls them from the settle-timer's worker thread.

Contract for subclasses:

    name         class attribute, matches the config section
    is_available() -> bool
    apply(theme) -> None

apply() should not raise on the "file not found" or "config empty"
paths; that is what is_available() is for. apply() may raise on
genuinely unexpected errors (disk full, permission denied), and the
engine will log them per-adapter without stopping the others.

Helpers in this file:

    cfg_value()      - read a config value with fallback
    resolve_path()   - expand ~ and %ENV% in a config path
    first_existing() - try a list of candidate paths

These exist so each adapter's apply() is mostly about the app's format,
not about Windows path quirks.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import log
from ..theme import Theme


class Adapter:
    """Base class. Subclasses fill in `name`, `is_available`, `apply`."""

    name: str = "unnamed"

    def __init__(self, cfg: dict):
        """`cfg` is the merged config slice for this adapter, from
        config.get_adapter(). It already includes `enabled` and any
        user overrides. Adapters read from `self.cfg` in apply()."""
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))

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
# Free functions used by adapters (and by tests)
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