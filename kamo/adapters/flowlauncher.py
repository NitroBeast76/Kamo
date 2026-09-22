"""Flow Launcher adapter.

Flow Launcher themes are XAML ResourceDictionaries. They live in
%APPDATA%\\FlowLauncher\\Themes\\, and the active theme is named in
%APPDATA%\\FlowLauncher\\Settings\\Settings.json under the "Theme" key
(matching the .xaml file's basename without extension).

Kamo does not edit the user's existing theme in place. It reads a
chosen base theme, substitutes colors into a copy, writes that copy
as Kamo.xaml, and points Flow Launcher at it. Flipping back to the
original theme takes one click in Flow Launcher's settings UI.

Reload: Flow Launcher reads its theme at startup. There is no
documented IPC for theme reload. Kamo logs a note and leaves the
running instance alone. Restart Flow Launcher (tray icon -> Quit,
then relaunch) to see the change.

Color substitution:

    The XAML is treated as text. Every hex token (#rgb, #rrggbb,
    #aarrggbb) is looked up in the color map, and replaced with the
    mapped role's color. Tokens not in the map are left alone.

    Map values can be:

        "text"          - a plain role name
        "base@0.20"     - role with an alpha channel applied, emitted
                          as #AARRGGBB
        "#123456"       - a literal hex, used as-is

    This is enough to express every color in the shipped themes, which
    are mostly opaque grays plus two semi-transparent blacks for the
    window background and the item selection highlight.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from ..theme import Theme


# Defaults tuned for CircleDarkBlur.xaml, which ships with Flow
# Launcher and is a common starting point. Replace any of these in
# kamo.toml if your base theme uses different hexes.
DEFAULT_COLOR_MAP: dict[str, str] = {
    # Foregrounds
    "#ffffff":   "text",
    "#fff":      "text",
    "#a3a3a3":   "subtext1",
    "#a8abb3":   "subtext1",
    "#c5d1da":   "subtext1",
    "#8a876e":   "subtext0",
    "#bfbfbf":   "subtext0",
    "#9da1aa":   "subtext0",
    # Accents
    "#aeaeae":   "accent",
    "#4d000000": "accent@0.30",
    # Surfaces
    "#242424":   "surface0",
    "#373737":   "surface1",
    "#a0a8b5":   "overlay1",
    # Window background
    "#33000000": "base@0.20",
}

DEFAULT_SETTINGS_CANDIDATES = [
    "%APPDATA%/FlowLauncher/Settings/Settings.json",
    "%APPDATA%/FlowLauncherPortable/Settings/Settings.json",
]

DEFAULT_THEMES_DIR_CANDIDATES = [
    "%APPDATA%/FlowLauncher/Themes",
    "%APPDATA%/FlowLauncherPortable/Themes",
]

# Full hex token: 3, 4, 6, or 8 hex digits after a #. Matched as a
# whole token so #fff is never substituted inside #ffffff.
_HEX_TOKEN = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


class FlowLauncherAdapter(Adapter):
    name = "flowlauncher"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.settings_path = self._resolve_settings()
        self.themes_dir = self._resolve_themes_dir()

        self.base_theme_name = self.cfg_value("base_theme", "CircleDarkBlur.xaml")
        self.output_theme_name = self.cfg_value("output_theme", "Kamo.xaml")
        self.theme_key = self.output_theme_name.rsplit(".xaml", 1)[0]
        self.color_map = self.cfg_dict("color_map", DEFAULT_COLOR_MAP)

    # ------------------------------------------------------------------

    def _resolve_settings(self) -> Path | None:
        override = self.cfg.get("settings")
        if override:
            p = resolve_path(override)
            return p if p.exists() else None
        return first_existing(DEFAULT_SETTINGS_CANDIDATES)

    def _resolve_themes_dir(self) -> Path | None:
        override = self.cfg.get("themes_dir")
        if override:
            p = resolve_path(override)
            return p if p.exists() else None
        return first_existing(DEFAULT_THEMES_DIR_CANDIDATES)

    def is_available(self) -> bool:
        if self.settings_path is None or self.themes_dir is None:
            return False
        base = self.themes_dir / self.base_theme_name
        return base.exists()

    # ------------------------------------------------------------------

    def apply(self, theme: Theme) -> None:
        if self.themes_dir is None or self.settings_path is None:
            self.log_warn("no settings or themes directory resolved; skipping")
            return

        base = self.themes_dir / self.base_theme_name
        if not base.exists():
            self.log_warn(f"base theme {base} not found; skipping")
            return

        try:
            source = read_text(base)
        except OSError as e:
            self.log_error(f"could not read {base}: {e}")
            return

        resolved = self._resolve_map(theme)
        if not resolved:
            self.log_warn("color map resolved to nothing; skipping")
            return

        output = self._substitute(source, resolved)

        out_path = self.themes_dir / self.output_theme_name
        try:
            write_text(out_path, output)
        except OSError as e:
            self.log_error(f"could not write {out_path}: {e}")
            return

        self.log_info(f"wrote {out_path.name}")

        if self._update_settings():
            self.log_info(
                "settings.json updated; restart Flow Launcher to apply"
            )

    # ------------------------------------------------------------------

    def _resolve_map(self, theme: Theme) -> dict[str, str]:
        """Turn the color_map (source hex -> role or role@alpha) into
        a lookup of source hex -> final #RRGGBB or #AARRGGBB string.
        """
        out: dict[str, str] = {}
        for source_hex, spec in self.color_map.items():
            spec = spec.strip()
            if not spec:
                continue

            # Literal hex?
            if spec.startswith("#"):
                out[source_hex.lower()] = spec.upper() if len(spec) == 9 else spec
                continue

            # role or role@alpha
            if "@" in spec:
                role, alpha_str = spec.split("@", 1)
                try:
                    alpha = float(alpha_str)
                except ValueError:
                    self.log_warn(f"bad alpha in map: {spec!r}")
                    continue
                if not theme.has(role):
                    self.log_warn(f"map refers to unknown role {role!r}")
                    continue
                hex_rgb = theme.get(role)
                out[source_hex.lower()] = C.with_alpha(hex_rgb, alpha)
                continue

            # Plain role
            if not theme.has(spec):
                self.log_warn(f"map refers to unknown role {spec!r}")
                continue
            out[source_hex.lower()] = theme.get(spec)

        return out

    # ------------------------------------------------------------------

    @staticmethod
    def _substitute(source: str, mapping: dict[str, str]) -> str:
        """Replace every hex token found in `mapping`; leave others alone."""
        def repl(m: re.Match) -> str:
            token = m.group(0).lower()
            return mapping.get(token, m.group(0))

        return _HEX_TOKEN.sub(repl, source)

    # ------------------------------------------------------------------

    def _update_settings(self) -> bool:
        if self.settings_path is None:
            return False
        try:
            data = json.loads(read_text(self.settings_path))
        except (OSError, json.JSONDecodeError) as e:
            self.log_warn(f"could not parse settings.json: {e}")
            return False

        if data.get("Theme") == self.theme_key:
            return False

        data["Theme"] = self.theme_key
        try:
            write_text(
                self.settings_path,
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            )
        except OSError as e:
            self.log_error(f"could not write settings.json: {e}")
            return False

        return True