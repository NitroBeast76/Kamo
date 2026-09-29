"""PyInstaller entry point.

Runs `kamo.__main__:main` as a proper package import rather than as
a top-level script. Without this, PyInstaller executes
`kamo/__main__.py` directly, relative imports have no parent package,
and the exe crashes with `ImportError: attempted relative import
with no known parent package`.

Console handling: the exe is built with `--console` so CLI flags
(`--version`, `--once`, `--resync`) can print output. When the app
launches with no arguments (tray mode), the console window is hidden
immediately so the user never sees it. The window is only hidden,
not detached — if the process crashes, the console is still
available for a debugger.
"""

import ctypes
import sys


def _hide_console_if_no_args() -> None:
    """Hide the console window when launched without CLI arguments.

    Tray mode has no interactive output, so the console would just be
    a black window flashing on screen for two seconds at login. Only
    hide it when argv[1:] is empty; explicit CLI invocations keep the
    console for printing.
    """
    if len(sys.argv) > 1:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass  # non-Windows, or console already gone


if __name__ == "__main__":
    _hide_console_if_no_args()
    from kamo.__main__ import main
    sys.exit(main())