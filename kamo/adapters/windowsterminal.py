"""Windows Terminal adapter.

Windows Terminal's settings.json is strict JSON. It contains a
`schemes` array, each scheme a flat object with a name and 20 color
keys (background, foreground, the 8 ANSI colors, and their 8 bright
variants).

Kamo edits the scheme named by `scheme_name` in kamo.toml, or - if
that key is absent or "auto" - the scheme that
`profiles.defaults.colorScheme` currently points at. Auto-detection
means Kamo works on any Windows Terminal setup without configuration:
whatever theme the user has selected is the one that gets recolored.
All other schemes are left alone. The scheme name itself is preserved.

Reload: Windows Terminal watches its settings.json and applies
changes live. No CLI call needed.

The file is parsed with json.loads and re-emitted with
json.dumps(indent=4). Windows Terminal's own editor uses 4-space
indent, so this round-trips cleanly. If the file contains // comments
(Windows Terminal tolerates them, strict JSON does not), parsing
fails and the adapter logs an error without touching the file. That
is the honest failure mode; silently stripping comments and rewriting
would be worse.

Map value syntax:

    "role"            - use the role's hex directly
    "role+0.15"       - brighten the role by 0.15 lightness
    "role-0.10"       - darken the role by 0.10 lightness
    "#123456"         - a literal hex, used as-is

Bright* keys default to a small brighten so the distinction between
`red` and `brightRed` survives the theme swap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from ..theme import Theme


# Windows Terminal scheme key -> map spec. Brights get a small
# brighten so they read as a distinct tier without jumping hue.
DEFAULT_ANSI_MAP: dict[str, str] = {
    "background":          "base",
    "foreground":          "text",
    "cursorColor":         "accent",
    "selectionBackground": "surface1",

    "black":   "surface0",
    "red":     "red",
    "green":   "green",
    "yellow":  "yellow",
    "blue":    "blue",
    "purple":  "mauve",
    "cyan":    "teal",
    "white":   "subtext1",

    "brightBlack":   "overlay0",
    "brightRed":     "red+0.10",
    "brightGreen":   "green+0.10",
    "brightYellow":  "yellow+0.10",
    "brightBlue":    "blue+0.10",
    "brightPurple":  "mauve+0.10",
    "brightCyan":    "teal+0.10",
    "brightWhite":   "text",
}

DEFAULT_SETTINGS_CANDIDATES = [
    # Store version (most common)
    "%LOCALAPPDATA%/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/"
    "LocalState/settings.json",
    # Preview build
    "%LOCALAPPDATA%/Packages/"
    "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json",
    # Unpackaged / portable / older installs
    "%LOCALAPPDATA%/Microsoft/Windows Terminal/settings.json",
]

# "role+N" or "role-N"
_SHIFTED = re.compile(r"^(?P<role>[a-z_][a-z0-9_]*)(?P<op>[+-])(?P<amount>\d*\.?\d+)$")


class WindowsTerminalAdapter(Adapter):
    name = "windowsterminal"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.path = self._resolve_path()
        # None or "auto" means "detect from settings.json's active
        # scheme". A string means the user named a specific scheme.
        raw_name = self.cfg_value("scheme_name", None)
        if isinstance(raw_name, str) and raw_name.lower() == "auto":
            raw_name = None
        self.scheme_name: str | None = raw_name
        self.ansi_map = self.cfg_dict("ansi_map", DEFAULT_ANSI_MAP)

    # ------------------------------------------------------------------

    def _resolve_path(self) -> Path | None:
        override = self.cfg.get("settings")
        if override and override != "auto":
            p = resolve_path(override)
            return p if p.exists() else None
        return first_existing(DEFAULT_SETTINGS_CANDIDATES)

    def _resolve_scheme_name(self, data: dict) -> str | None:
        """Determine which scheme to target.

        Order:
            1. Explicit `scheme_name` in kamo.toml.
            2. `profiles.defaults.colorScheme` in settings.json.
            3. None, in which case the caller logs and skips.

        Auto-detection is what makes this work on any Windows
        Terminal install without a user editing kamo.toml.
        """
        if self.scheme_name:
            return self.scheme_name

        profiles = data.get("profiles")
        if isinstance(profiles, dict):
            defaults = profiles.get("defaults")
            if isinstance(defaults, dict):
                name = defaults.get("colorScheme")
                if isinstance(name, str) and name:
                    self.log_info(f"auto-detected scheme {name!r}")
                    return name

        return None

    def is_available(self) -> bool:
        return self.path is not None and self.path.exists()

    # ------------------------------------------------------------------

    def apply(self, theme: Theme) -> None:
        if self.path is None:
            self.log_warn("no settings.json resolved; skipping")
            return

        try:
            text = read_text(self.path)
        except OSError as e:
            self.log_error(f"could not read {self.path}: {e}")
            return

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            self.log_error(
                f"settings.json is not valid JSON ({e}); "
                "if it has // comments, remove them or the adapter "
                "cannot edit the scheme safely"
            )
            return

        schemes = data.get("schemes")
        if not isinstance(schemes, list):
            self.log_warn("settings.json has no 'schemes' array")
            return

        scheme_name = self._resolve_scheme_name(data)
        if scheme_name is None:
            self.log_warn(
                "no colorScheme found in settings.json and no scheme_name "
                "in kamo.toml; nothing to do"
            )
            return

        target = None
        for s in schemes:
            if isinstance(s, dict) and s.get("name") == scheme_name:
                target = s
                break

        if target is None:
            self.log_warn(
                f"scheme {scheme_name!r} not found in settings.json; "
                f"available: {[s.get('name') for s in schemes if isinstance(s, dict)]}"
            )
            return

        resolved = self._resolve_map(theme)
        if not resolved:
            self.log_warn("ansi_map resolved to nothing; skipping")
            return

        changes = 0
        for key, hex_val in resolved.items():
            if target.get(key) != hex_val:
                target[key] = hex_val
                changes += 1

        if changes == 0:
            self.log_info(f"scheme {scheme_name!r} already up to date")
            return

        output = json.dumps(data, indent=4, ensure_ascii=False) + "\n"

        try:
            write_text(self.path, output)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        self.log_info(
            f"updated {changes} color(s) in scheme {scheme_name!r}"
        )

    # ------------------------------------------------------------------

    def _resolve_map(self, theme: Theme) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, spec in self.ansi_map.items():
            spec = spec.strip()
            if not spec:
                continue

            # Literal hex passthrough.
            if spec.startswith("#"):
                out[key] = spec
                continue

            # role+N / role-N
            m = _SHIFTED.match(spec)
            if m:
                role = m.group("role")
                amount = float(m.group("amount"))
                if m.group("op") == "-":
                    amount = -amount
                if not theme.has(role):
                    self.log_warn(f"{key!r} refers to unknown role {role!r}")
                    continue
                base = theme.get(role)
                if amount > 0:
                    out[key] = C.brighten(base, dL=amount)
                elif amount < 0:
                    out[key] = C.darken(base, dL=-amount)
                else:
                    out[key] = base
                continue

            # Plain role.
            if not theme.has(spec):
                self.log_warn(f"{key!r} refers to unknown role {spec!r}")
                continue
            out[key] = theme.get(spec)

        return out