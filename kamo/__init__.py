"""Kamo — wallpaper-driven theme sync.

The package exposes a version string used by the CLI and by the log
header. Nothing else is re-exported at the top level; users import
from submodules directly (`from kamo.engine import Engine`, etc.).
Keeping the top-level namespace thin avoids accidental circular
imports between config, log, and adapters.

Entry point: `python -m kamo`.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]