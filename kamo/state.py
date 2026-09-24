"""Persistent state for Kamo.

Some adapters need to remember what they wrote last time, because
their config format has no fixed "role slot" — Kamo replaces hex
values in place, and once replaced, the original hex no longer
exists to match against on the next apply.

The canonical example is fastfetch: the config has inline hexes
like `"#FF8200"`, and Kamo's job is to swap them for the theme's
accent. On the first apply, the original `#FF8200` matches. On the
second apply, `#FF8200` is gone (replaced by the previous theme's
accent) and a naive search finds nothing. With state, Kamo knows
it wrote `#a24b96` last time and looks for that instead.

State is a plain JSON file at ~/.config/kamo/state.json. It is
written atomically (temp file + rename). Reads are tolerant: a
missing or corrupt file returns an empty dict rather than raising.

Schema, by convention:

    {
      "fastfetch": {
        "inline": {
          "accent": "#a24b96",
          "text":   "#e4e3eb",
          ...
        }
      },
      "yasb": { ... },
      ...
    }

Adapters may store whatever key/value pairs they need. Nothing
else in Kamo reads this file; it is opaque to the engine.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


STATE_PATH = Path.home() / ".config" / "kamo" / "state.json"

# Guards read-modify-write sequences. Adapters call put() from the
# settle worker thread; the tray might call get() from the main
# thread. Rare, but the lock costs nothing.
_LOCK = threading.RLock()


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def load() -> dict[str, Any]:
    """Return the full state dict. Empty on missing or corrupt file."""
    with _LOCK:
        try:
            raw = STATE_PATH.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data


def save(state: dict[str, Any]) -> None:
    """Write the full state dict atomically. Silently no-ops on
    write errors — a broken state file must not break the tray.
    """
    with _LOCK:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".json.kamo-tmp")
            tmp.write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp.replace(STATE_PATH)
        except OSError:
            pass


def get(adapter: str, key: str, default: Any = None) -> Any:
    """Read one value: state[adapter][key]. Returns `default` if
    either level is missing.
    """
    data = load()
    section = data.get(adapter)
    if not isinstance(section, dict):
        return default
    return section.get(key, default)


def put(adapter: str, key: str, value: Any) -> None:
    """Write one value: state[adapter][key] = value. Preserves every
    other key under every adapter.
    """
    with _LOCK:
        data = load()
        section = data.get(adapter)
        if not isinstance(section, dict):
            section = {}
        section[key] = value
        data[adapter] = section
        save(data)


def put_many(adapter: str, updates: dict[str, Any]) -> None:
    """Write multiple keys under one adapter in a single round-trip."""
    if not updates:
        return
    with _LOCK:
        data = load()
        section = data.get(adapter)
        if not isinstance(section, dict):
            section = {}
        section.update(updates)
        data[adapter] = section
        save(data)


def clear(adapter: str) -> None:
    """Remove everything under one adapter. Used by tests and by a
    'reset' path if one ever gets wired up.
    """
    with _LOCK:
        data = load()
        if adapter in data:
            del data[adapter]
            save(data)