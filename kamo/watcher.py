"""Wallpaper change detection.

Kamo needs to know "did the wallpaper change?" and, if it did, get an
image it can extract colors from.

Two modes:

    static  - Windows stores the current wallpaper path in the
              registry. We fingerprint (path, mtime, size). Cheap and
              exact.

    live    - Lively, Wallpaper Engine, and friends redraw constantly
              and don't touch the registry. There is no OS event for
              this. We take a low-resolution screen grab and hash it.
              Every poll produces a new hash; the engine's settle
              timer eats the noise.

The public surface:

    current()           -> (kind, fingerprint)
    get_image(kind)     -> path to an image file, or None

Both are safe to call from any thread. They do not raise on the
"nothing available" path; they return ("", "") or None.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

try:
    import psutil
except ImportError:
    psutil = None

try:
    import mss
except ImportError:
    mss = None


Kind = Literal["file", "screen"]

# Process names that indicate a live wallpaper engine. Compared
# case-insensitively. Add new engines here as you meet them.
LIVE_PROCESS_NAMES = {
    "lively.exe",
    "wallpaper64.exe",
    "wallpaper32.exe",
    "webwallpaper32.exe",
    "wallpaperengine.exe",
}

# Where the frame for a live wallpaper gets written. Overwritten on
# every capture; no history.
FRAME_DIR = Path.home() / ".cache" / "kamo"
FRAME_PATH = FRAME_DIR / "frame.png"

# Size of the screen grab used for fingerprinting. Small enough to be
# fast (a few ms), big enough to distinguish most wallpapers.
FINGERPRINT_SIZE = 256


# ---------------------------------------------------------------------
# Static wallpaper
# ---------------------------------------------------------------------

def _static_path() -> str | None:
    """Current wallpaper path from the registry, or None."""
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "WallPaper")
            return value or None
    except OSError:
        return None


def _file_fingerprint(path: str) -> str:
    """Hash of (path, mtime_ns, size). Empty string if the file is gone."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    h = hashlib.blake2b(digest_size=8)
    h.update(path.encode("utf-8", errors="replace"))
    h.update(b"\0")
    h.update(str(st.st_mtime_ns).encode())
    h.update(b"\0")
    h.update(str(st.st_size).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------
# Live wallpaper
# ---------------------------------------------------------------------

def _has_live_wallpaper() -> bool:
    if psutil is None:
        return False
    try:
        names = {p.info["name"] for p in psutil.process_iter(["name"])
                 if p.info.get("name")}
    except Exception:
        return False
    return any(n.lower() in LIVE_PROCESS_NAMES for n in names)


def _screen_fingerprint() -> str:
    """Low-res grab of the top-left region, hashed. Empty on failure."""
    if mss is None:
        return ""
    try:
        with mss.mss() as sct:
            mon = sct.monitors[1]
            region = {
                "left": mon["left"],
                "top": mon["top"],
                "width": FINGERPRINT_SIZE,
                "height": FINGERPRINT_SIZE,
            }
            shot = sct.grab(region)
            return hashlib.blake2b(shot.rgb, digest_size=8).hexdigest()
    except Exception:
        return ""


# ---------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------

def current() -> tuple[str, str]:
    """Return (kind, fingerprint).

    kind is "file" or "screen" (or "" if neither is available).
    fingerprint is a short hex string. Two calls with the same
    wallpaper in the same state return the same value.
    """
    if _has_live_wallpaper():
        return ("screen", _screen_fingerprint())

    path = _static_path()
    if not path:
        return ("", "")
    return ("file", _file_fingerprint(path))


def get_image(kind: str) -> str | None:
    """Return a path to an image file, or None.

    For kind == "file": returns the registry wallpaper path.
    For kind == "screen": captures a frame to FRAME_PATH and returns
                          that path.
    """
    if kind == "file":
        path = _static_path()
        if path and Path(path).exists():
            return path
        return None

    if kind == "screen":
        return _capture_frame()

    return None


def _capture_frame() -> str | None:
    if mss is None:
        return None
    try:
        FRAME_DIR.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            sct.shot(mon=1, output=str(FRAME_PATH))
        if FRAME_PATH.exists() and FRAME_PATH.stat().st_size > 0:
            return str(FRAME_PATH)
        return None
    except Exception:
        return None


def is_live() -> bool:
    """Convenience: is a live wallpaper engine running right now?"""
    return _has_live_wallpaper()