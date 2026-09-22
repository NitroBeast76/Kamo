"""Adapter registry.

The engine never imports an adapter module directly. It asks this
module for the list of adapter classes, instantiates each one with
its config slice, and keeps the ones that report is_available().

Adding a new adapter is two lines here:

    1. Import the class.
    2. Add it to REGISTRY.

The config template and kamo.toml.example don't need to change for
the adapter to work; they only document overridable keys. An adapter
with no user overrides still runs with its built-in defaults.
"""

from __future__ import annotations

from .base import Adapter
from .cava import CavaAdapter
from .chronoterm import ChronotermAdapter
from .fastfetch import FastfetchAdapter
from .flowlauncher import FlowLauncherAdapter
from .glazewm import GlazewmAdapter
from .windowsterminal import WindowsTerminalAdapter
from .yasb import YasbAdapter


# Display order in logs. Also the order the engine iterates when
# applying, which matters if two adapters ever touch the same file
# (none currently do).
REGISTRY: list[type[Adapter]] = [
    YasbAdapter,
    GlazewmAdapter,
    CavaAdapter,
    ChronotermAdapter,
    FlowLauncherAdapter,
    FastfetchAdapter,
    WindowsTerminalAdapter,
]


def load_adapters(config: dict) -> list[Adapter]:
    """Instantiate every known adapter with its config slice.

    Returns only the ones that are enabled AND available on this
    machine. An adapter is:
      - enabled    : its `enabled` key in config is truthy
      - available  : its target app / config file exists

    Adapters that fail during construction (bad config, missing
    optional dependency, unexpected schema) are logged and skipped
    rather than aborting the whole list.

    `config` here is the full merged config dict from
    config.load().
    """
    from .. import config as config_mod
    from .. import log

    active: list[Adapter] = []

    for cls in REGISTRY:
        name = getattr(cls, "name", cls.__name__)
        try:
            slice_ = config_mod.get_adapter(config, name)
        except Exception as e:
            log.warn(f"[{name}] config slice failed: {e}")
            continue

        if not slice_.get("enabled", True):
            log.info(f"[{name}] disabled in config")
            continue

        try:
            inst = cls(slice_)
        except Exception as e:
            log.error(f"[{name}] construction failed: {e}")
            continue

        try:
            available = inst.is_available()
        except Exception as e:
            log.error(f"[{name}] is_available() raised: {e}")
            continue

        if not available:
            log.info(f"[{name}] not available on this machine")
            continue

        active.append(inst)

    return active


__all__ = [
    "Adapter",
    "REGISTRY",
    "load_adapters",
    "YasbAdapter",
    "GlazewmAdapter",
    "CavaAdapter",
    "ChronotermAdapter",
    "FlowLauncherAdapter",
    "FastfetchAdapter",
    "WindowsTerminalAdapter",
]