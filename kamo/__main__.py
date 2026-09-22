"""Kamo entry point.

Two modes:

    python -m kamo            - tray app (default)
    python -m kamo --once IMG - extract and print a theme, no tray
    python -m kamo --resync   - one-shot apply, no tray, then exit
    python -m kamo --version  - print version and exit

The tray uses pystray and shows:

    - Status line: "Ready", "Pending (12s)", "Paused", "Error"
    - Adapter list (informational)
    - Pause / Resume
    - Apply now
    - Resync
    - Run at login (toggle)
    - Open config folder
    - Open log file
    - Quit

Threading: pystray runs its own event loop on the main thread. The
Engine runs on a background thread. Menu callbacks fire on the tray
thread and call Engine methods, which are thread-safe. The Engine's
status callback fires on the settle worker thread and updates the
icon and title, which pystray allows from any thread.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__ as VERSION
from . import autostart
from . import color as C
from . import config as config_mod
from . import log
from .engine import Engine
from . import palette


# ---------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------

def _cmd_once(image_path: str) -> int:
    try:
        theme = palette.build_theme(image_path)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    width = max(len(name) for name in theme.role_names())
    for role in theme.role_names():
        value = theme.get(role)
        L, c, H = C.hex_to_oklch(value)
        print(f"{role:<{width}}  {value}   L={L:.2f} C={c:.3f} H={H:5.1f}")
    return 0


def _cmd_resync() -> int:
    cfg = config_mod.load()
    engine = Engine(cfg)
    engine.apply_now()
    # Give the worker a moment to finish before we exit.
    time.sleep(0.5)
    return 0


# ---------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------

# Cache of tinted icons, keyed by (color, bad). pystray calls the icon
# setter often; creating a new PIL image each time is wasteful.
_ICON_CACHE: dict[tuple[str, bool], "Image.Image"] = {}
_ICON_SOURCE: "Image.Image | None" = None
_ICON_SOURCE_LOADED: bool = False


def _icon_source_path() -> Path:
    """Where kamo.ico lives, whether running from source or from a
    PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base / "kamo.ico"


def _load_icon_source() -> "Image.Image | None":
    """Load and cache the source icon. Returns None if unavailable."""
    global _ICON_SOURCE, _ICON_SOURCE_LOADED
    if _ICON_SOURCE_LOADED:
        return _ICON_SOURCE
    _ICON_SOURCE_LOADED = True

    try:
        from PIL import Image
        path = _icon_source_path()
        if path.exists():
            _ICON_SOURCE = Image.open(path).convert("RGBA")
        else:
            log.warn(f"icon not found at {path}, using fallback")
            _ICON_SOURCE = None
    except Exception as e:
        log.warn(f"could not load icon: {e}")
        _ICON_SOURCE = None

    return _ICON_SOURCE


def _draw_fallback(color: str) -> "Image.Image":
    """The pre-icon placeholder: a filled circle with a hole."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color)
    d.ellipse((22, 22, 42, 42), fill="#1e1e2e")
    return img


def _tint(source: "Image.Image", color: str) -> "Image.Image":
    """Multiply the source's RGB by the target color, keep alpha.

    A white/light icon becomes the target color; a darker icon becomes
    a darkened version. This preserves any detail or shading in the
    source icon while shifting its hue to match the accent.
    """
    from PIL import Image

    tr, tg, tb = C.hex_to_rgb(color)
    src = source.convert("RGBA")

    r, g, b, a = src.split()
    r = r.point(lambda v: v * tr // 255)
    g = g.point(lambda v: v * tg // 255)
    b = b.point(lambda v: v * tb // 255)
    return Image.merge("RGBA", (r, g, b, a))


def _make_icon(color: str = "#89b4fa", bad: bool = False) -> "Image.Image":
    """Return a tray icon tinted to `color` (or error red if bad)."""
    if bad:
        color = "#f38ba8"

    key = (color, bad)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    source = _load_icon_source()
    if source is None:
        icon = _draw_fallback(color)
    else:
        try:
            icon = _tint(source, color)
        except Exception as e:
            log.warn(f"tinting failed ({e}); using fallback")
            icon = _draw_fallback(color)

    _ICON_CACHE[key] = icon
    return icon


def _run_tray(engine: Engine) -> int:
    try:
        import pystray
    except ImportError:
        print(
            "error: pystray is not installed. "
            "Install with: pip install pystray pillow",
            file=sys.stderr,
        )
        return 1

    # --- menu actions --------------------------------------------------

    def on_pause(icon, item):
        engine.toggle_pause()
        icon.update_menu()

    def on_apply_now(icon, item):
        engine.apply_now()

    def on_resync(icon, item):
        engine.resync()

    def on_toggle_autostart(icon, item):
        if autostart.is_enabled():
            ok = autostart.disable()
            if ok:
                log.info("autostart disabled")
            else:
                log.warn("could not disable autostart (see log)")
        else:
            ok = autostart.enable()
            if ok:
                log.info("autostart enabled")
            else:
                log.warn("could not enable autostart (see log)")
        icon.update_menu()

    def on_open_config(icon, item):
        _open_path(config_mod.CONFIG_DIR)

    def on_open_log(icon, item):
        _open_path(log.LOG_PATH)

    def on_quit(icon, item):
        engine.stop()
        icon.stop()

    # --- menu ----------------------------------------------------------

    def status_text(item):
        if engine.is_paused:
            return "Paused"
        if engine.is_pending:
            return "Pending…"
        if engine.last_error:
            return "Error (see log)"
        if engine.last_apply_at:
            ago = int(time.time() - engine.last_apply_at)
            return f"Ready (applied {ago}s ago)"
        return "Ready"

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: status_text(item),
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Pause",
            on_pause,
            checked=lambda item: engine.is_paused,
        ),
        pystray.MenuItem("Apply now", on_apply_now),
        pystray.MenuItem("Resync", on_resync),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Run at login",
            on_toggle_autostart,
            checked=lambda item: autostart.is_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open config folder", on_open_config),
        pystray.MenuItem("Open log", on_open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        "Kamo",
        _make_icon(),
        "Kamo",
        menu,
    )

    # --- status callback ----------------------------------------------

    def on_status(theme, error):
        try:
            if error:
                icon.icon = _make_icon(bad=True)
                icon.title = f"Kamo — error: {error[:60]}"
            elif theme is not None:
                icon.icon = _make_icon(color=theme.accent)
                icon.title = f"Kamo — {theme.base} on {theme.accent}"
        except Exception:
            pass  # icon updates are best-effort

    engine.on_status(on_status)

    # --- run ----------------------------------------------------------

    engine.start()
    log.info(f"Kamo {VERSION} ready")
    icon.run()
    log.info("tray exited")
    return 0


# ---------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------

def _open_path(path: Path) -> None:
    """Open a file or folder in the OS default handler.

    Uses os.startfile on Windows, which is the polite way — it hands
    off to Explorer and doesn't block. Falls back to a subprocess on
    other platforms so the code still runs if you develop on Linux.
    """
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        log.warn(f"could not open {path}: {e}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kamo",
        description="Wallpaper-driven theme sync for Windows.",
    )
    p.add_argument(
        "--once",
        metavar="IMAGE",
        help="Extract a theme from IMAGE, print it, and exit.",
    )
    p.add_argument(
        "--resync",
        action="store_true",
        help="Apply the current wallpaper once and exit (no tray).",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"Kamo {VERSION}",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)

    # Load config first so log level is set before anything else logs.
    cfg = config_mod.load()

    if args.once:
        return _cmd_once(args.once)

    if args.resync:
        return _cmd_resync()

    # Tray mode. Build the engine here so adapter construction errors
    # are logged before the tray icon appears.
    try:
        engine = Engine(cfg)
    except Exception as e:
        log.error(f"engine construction failed: {e}")
        return 1

    return _run_tray(engine)


if __name__ == "__main__":
    sys.exit(main())