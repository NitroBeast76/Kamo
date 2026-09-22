"""Engine: the conductor.

Responsibilities:
    - Own the watcher loop (a background thread).
    - Own the settle timer.
    - On settle, build a Theme from the current wallpaper and hand it
      to every active adapter.
    - Track state the tray needs: paused, last theme, last error, next
      scheduled apply.

It does not talk to the tray directly. The tray polls state via
properties and calls methods (pause, resume, resync, apply_now). All
public methods are thread-safe.

Order of operations on a wallpaper change:

    1. watcher.current() returns a (kind, fingerprint)
    2. If fingerprint != last_fp, poke the settle timer.
    3. settle timer waits `settle_delay` seconds of quiet.
    4. On fire: watcher.get_image(kind) -> path
    5. palette.build_theme(path) -> Theme
    6. For each adapter, adapter.apply(theme), errors logged per-adapter.
    7. Emit a callback so the tray can refresh its icon/menu.

If the wallpaper changes again mid-settle, step 2 re-pokes and the
timer restarts. If it changes mid-apply, step 6 completes (partial
state is possible for a fraction of a second), then the next poke
starts a fresh cycle.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import color as C  # noqa: F401  (re-exported for callers)
from . import config as config_mod
from . import log
from . import palette
from . import settle as settle_mod
from . import watcher
from .adapters import load_adapters
from .theme import Theme


# Callback signature: fn(theme: Theme | None, error: str | None) -> None.
# Called after every apply attempt, success or failure. Runs on the
# settle-timer worker thread; callbacks must be fast and thread-safe.
StatusCallback = Callable[[Theme | None, str | None], None]


class Engine:
    def __init__(self, config: dict | None = None):
        self.config = config if config is not None else config_mod.load()

        general = config_mod.get_general(self.config)
        self.poll_interval: float = float(general.get("poll_interval", 5.0))
        self.settle_delay: float = float(general.get("settle_delay", 20.0))

        self.adapters = load_adapters(self.config)
        log.info(f"active adapters: {[a.name for a in self.adapters]}")

        # State
        self._paused = False
        self._last_fp: tuple[str, str] | None = None
        self._last_theme: Theme | None = None
        self._last_error: str | None = None
        self._last_apply_at: float | None = None
        self._pending: bool = False          # settle timer running?
        self._state_lock = threading.Lock()

        # Status callback list; engine invokes all of them.
        self._callbacks: list[StatusCallback] = []

        # Settle timer + worker thread
        self._settle = settle_mod.SettleTimer(self.settle_delay, self._apply)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watcher thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            self._last_fp = watcher.current()
        except Exception as e:
            log.warn(f"initial wallpaper read failed: {e}")
            self._last_fp = ("", "")
        self._thread = threading.Thread(
            target=self._loop, name="kamo-watch", daemon=True
        )
        self._thread.start()
        log.info(
            f"engine started (poll={self.poll_interval}s, "
            f"settle={self.settle_delay}s)"
        )

    def stop(self) -> None:
        """Signal the watcher thread and the settle timer to stop."""
        self._stop.set()
        self._settle.cancel()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        log.info("engine stopped")

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def pause(self) -> None:
        with self._state_lock:
            self._paused = True
        self._settle.cancel()
        log.info("paused")
        self._notify()

    def resume(self) -> None:
        with self._state_lock:
            self._paused = False
        log.info("resumed")
        self._notify()

    def toggle_pause(self) -> None:
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def resync(self) -> None:
        """Forget the last fingerprint and re-evaluate immediately.

        Useful after the user has manually edited a config and wants
        Kamo to reapply without waiting for a wallpaper change.
        """
        try:
            self._last_fp = watcher.current()
        except Exception as e:
            log.warn(f"resync read failed: {e}")
            self._last_fp = ("", "")
        self._settle.poke(self._last_fp)
        log.info("resync requested")

    def apply_now(self) -> None:
        """Skip the settle window and apply on the next tick.

        The tray uses this when the user picks "Apply now" and doesn't
        want to wait `settle_delay` seconds.
        """
        self._settle.flush()

    def on_status(self, cb: StatusCallback) -> None:
        """Register a callback invoked after every apply attempt."""
        self._callbacks.append(cb)

    # ------------------------------------------------------------------
    # State (read-only from outside)
    # ------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    @property
    def last_theme(self) -> Theme | None:
        with self._state_lock:
            return self._last_theme

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    @property
    def last_apply_at(self) -> float | None:
        with self._state_lock:
            return self._last_apply_at

    @property
    def is_pending(self) -> bool:
        with self._state_lock:
            return self._pending

    @property
    def adapter_names(self) -> list[str]:
        return [a.name for a in self.adapters]

    # ------------------------------------------------------------------
    # Watcher loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.is_paused:
                    self._tick()
            except Exception as e:
                log.error(f"watcher tick failed: {e}")
            # Sleep in small slices so stop() returns promptly.
            slept = 0.0
            while slept < self.poll_interval and not self._stop.is_set():
                time.sleep(0.2)
                slept += 0.2

    def _tick(self) -> None:
        try:
            fp = watcher.current()
        except Exception as e:
            log.warn(f"watcher.current() raised: {e}")
            return

        if fp == self._last_fp:
            return

        # Change detected.
        self._last_fp = fp
        with self._state_lock:
            self._pending = True
        log.info(f"wallpaper changed ({fp[0]}), settling {self.settle_delay:.0f}s")
        self._settle.poke(fp)
        self._notify()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply(self, payload) -> None:
        """Called by the settle timer. Runs on its worker thread."""
        with self._state_lock:
            self._pending = False

        kind = payload[0] if isinstance(payload, tuple) and payload else ""
        theme: Theme | None = None
        error: str | None = None

        try:
            image_path = watcher.get_image(kind)
            if not image_path:
                raise RuntimeError(f"no image available for kind={kind!r}")

            log.info(f"extracting theme from {image_path}")
            theme = palette.build_theme(image_path)
        except Exception as e:
            error = f"palette build failed: {e}"
            log.error(error)

        if theme is not None:
            self._dispatch(theme)

        with self._state_lock:
            self._last_theme = theme
            self._last_error = error
            self._last_apply_at = time.time()

        self._notify(theme, error)

    def _dispatch(self, theme: Theme) -> None:
        """Hand the theme to every adapter. Errors are per-adapter."""
        for adapter in self.adapters:
            try:
                adapter.apply(theme)
            except Exception as e:
                # adapter.apply is expected to log its own warnings;
                # this catch is for unexpected raises.
                log.error(f"[{adapter.name}] apply raised: {e}")

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify(
        self,
        theme: Theme | None = None,
        error: str | None = None,
    ) -> None:
        for cb in list(self._callbacks):
            try:
                cb(theme, error)
            except Exception as e:
                log.warn(f"status callback failed: {e}")