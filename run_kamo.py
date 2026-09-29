"""PyInstaller entry point.

Runs `kamo.__main__:main` as a proper package import rather than as
a top-level script. Without this, PyInstaller executes
`kamo/__main__.py` directly, relative imports have no parent package,
and the exe crashes with `ImportError: attempted relative import
with no known parent package`.
"""

import sys

from kamo.__main__ import main

if __name__ == "__main__":
    sys.exit(main())