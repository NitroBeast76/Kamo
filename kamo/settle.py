"""Debounce timer.

A wallpaper doesn't change once - it changes many times in quick
succession while the user browses a gallery, drags a live wallpaper
around, or opens a slideshow. Applying a theme on every intermediate
frame would thrash every app on the system.

SettleTimer fires the callback once, after the value has been stable
for `delay` seconds. Every poke resets the timer. If pokes keep
coming, nothing fires.

Thread-safe. Callbacks run on a worker thread, not on the caller's
thread. Exceptions inside the callback are caught and logged so a
broken adapter can't kill the timer thread.
"""

from __future__ import annotations

import threading

from . import log


class SettleTimer:
    """Fire `callback(payload)` once `payload` has been stable for
    `delay` seconds.

    Typical usage:

        timer = SettleTimer(20.0, on_settled)
        # ...on each observed change:
        timer.poke(new_payload)

    `payload` is passed through to the callback verbatim. It can be
    None, a tuple, whatever. Only one payload is stored at a time; the
    latest poke wins.
    """

    def __init__(self, delay: float, callback):
        if delay <= 0:
            raise ValueError("delay must be > 0")
        self.delay = float(delay)
        self.callback = callback
        self._timer: threading.Timer | None = None
        self._payload = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def poke(self, payload=None) -> None:
        """Register a change. Restarts the settle window."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._payload = payload
            self._timer = threading.Timer(self.delay, self._fire)
            # Daemon so a pending settle doesn't block interpreter exit.
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        """Cancel any pending fire. No-op if nothing is pending."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._payload = None

    def flush(self) -> None:
        """Fire immediately with the current payload, if any.

        Cancels the pending timer first. Used by the tray's
        'Apply now' action when the user doesn't want to wait.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            payload = self._payload
            self._payload = None
        if payload is not None:
            self._invoke(payload)

    # ------------------------------------------------------------------

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            payload = self._payload
            self._payload = None
        self._invoke(payload)

    def _invoke(self, payload) -> None:
        try:
            self.callback(payload)
        except Exception as e:
            log.error(f"settle callback failed: {e}")