"""yasb adapter.

yasb reads styles.css. Historically, users configure it three
different ways:

    1. Pure import. styles.css is just `@import "yasb_colors.css";`
       plus layout. All colors come from yasb_colors.css.

    2. Variables. styles.css has `:root { --background: ...; }`
       plus `.widget { background: var(--background); }`. The
       variable declarations might be inline in styles.css, or in
       an imported yasb_colors.css.

    3. Hardcoded. styles.css has no variables. Every color is a
       literal hex in the rules themselves.

Kamo handles all three, in one pass per file:

    yasb_colors.css    Always written with the canonical :root
                       variable block. Harmless if unused.

    styles.css         Two substitutions, in order:

                       a) Variable declarations. Any line matching
                          `--name: value;` where `name` is in the
                          variables map gets its value replaced
                          with the theme's color for that role.

                       b) Hex literals. Every `#rgb` / `#rrggbb` /
                          `#rrggbbaa` token not claimed by (a) gets
                          classified into a role and replaced.
                          Classification order:
                              - user color_map in kamo.toml
                              - previous write (from state.json)
                              - auto: hue for saturated colors,
                                lightness band for neutrals
                          Alpha channel is preserved.

Because hex literals are searched by value, and their values
change on every apply, Kamo keeps a `hex -> role` mapping in
state.json. Without it, the second apply would find no matches
and the file would freeze.

Reserved hexes in config are never touched.

Reload: yasb watches styles.css when `watch_stylesheet: true`. But
its watcher fires on content changes, not mtime, so Kamo rewrites a
marker comment at the bottom of styles.css on every real change. If
restart = true (default) and yasb.exe is running, Kamo also kills
and relaunches it as a fallback.

Nothing is written, no marker is bumped, and no restart happens if
the substitutions produce byte-identical output.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from .. import state as state_mod
from ..theme import Theme


# ---------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------

# Marker comment rewritten on content changes. Must be a content
# change, not an mtime change, to fire yasb's file watcher.
_MARKER_RE = re.compile(r"/\*\s*kamo-update:\s*\d+\s*\*/")

# Any `--name: value;` declaration, anywhere in the file.
_VAR_DECL = re.compile(r"(--[\w-]+\s*:\s*)([^;\r\n]+)(\s*;)")

# A hex token: `#rgb`, `#rgba`, `#rrggbb`, or `#rrggbbaa`.
_HEX_TOKEN = re.compile(r"#([0-9A-Fa-f]{3,8})\b")


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

# State file key. The value stored under this key is a dict of
# {"#rrggbb": "role"} — every hex Kamo has written or classified.
_STATE_KEY = "hex_map"

# CSS variable name (without --) -> Theme role. Users with unusual
# variable names override this via `variables` in kamo.toml.
DEFAULT_VARIABLES: dict[str, str] = {
    "background":  "base",
    "background2": "surface0",
    "accent":      "accent",
    "accentText":  "accent_text",
    "text":        "text",
    "subtext":     "subtext0",
    "hover":       "surface1",
    "mutedBG":     "mantle",
    "border":      "surface2",
    "redFlash":    "red",
}

# User's explicit pinning: source hex -> role. Overrides auto.
DEFAULT_COLOR_MAP: dict[str, str] = {}

# Hexes that Kamo must never modify. Stored without the leading #,
# lowercase. Config users write them with the # for readability.
DEFAULT_RESERVED_HEXES: list[str] = []

DEFAULT_DIR_CANDIDATES = [
    "~/.config/yasb",
    "~/AppData/Roaming/yasb",
    "~/AppData/Local/yasb",
]

# Hue anchors for classification of saturated hexes. Matches the
# anchors used by palette.py so classification is consistent with
# theme generation. Kept local to avoid a cross-module import.
_HUE_ANCHORS: dict[str, float] = {
    "red":    25.0,
    "green":  145.0,
    "yellow": 85.0,
    "blue":   250.0,
    "mauve":  310.0,
    "teal":   180.0,
    "peach":  50.0,
    "pink":   350.0,
    "sky":    220.0,
}


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------

class YasbAdapter(Adapter):
    name = "yasb"

    process_name = "yasb.exe"
    launch_command = ["yasb"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.dir = self._resolve_dir()
        self.colors_file = self.cfg_value("colors_file", "yasb_colors.css")
        self.styles_file = self.cfg_value("styles_file", "styles.css")
        self.variables = self.cfg_dict("variables", DEFAULT_VARIABLES)
        self.color_map = self.cfg_dict("color_map", DEFAULT_COLOR_MAP)
        self.reserved_hexes = {
            h.lstrip("#").lower()
            for h in self.cfg_value(
                "reserved_hexes", DEFAULT_RESERVED_HEXES
            )
        }

        self.restart = bool(self.cfg_value("restart", True))
        if not self.restart:
            self.process_name = None

        cmd = self.cfg_value("launch_command", None)
        if isinstance(cmd, list) and cmd:
            self.launch_command = cmd

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_dir(self) -> Path | None:
        override = self.cfg.get("dir")
        if override:
            p = resolve_path(override)
            return p if p.exists() else None
        return first_existing(DEFAULT_DIR_CANDIDATES)

    def _colors_path(self) -> Path | None:
        if not self.dir:
            return None
        return self.dir / self.colors_file

    def _styles_path(self) -> Path | None:
        if not self.dir:
            return None
        p = self.dir / self.styles_file
        return p if p.exists() else None

    def is_available(self) -> bool:
        if not self.dir or not self.dir.exists():
            return False
        return self._styles_path() is not None

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, theme: Theme) -> None:
        colors_path = self._colors_path()
        if colors_path is None:
            self.log_warn("no config directory resolved; skipping")
            return

        changed = False

        # 1. yasb_colors.css — always written if different.
        css = self._render_colors(theme)
        try:
            existing = read_text(colors_path)
        except OSError:
            existing = ""
        if css != existing:
            try:
                write_text(colors_path, css)
                changed = True
                self.log_info(f"wrote {colors_path.name}")
            except OSError as e:
                self.log_error(f"could not write {colors_path}: {e}")
        else:
            self.log_info(f"{colors_path.name} already up to date")

        # 2. styles.css — variable substitution, then hex substitution.
        styles_path = self._styles_path()
        if styles_path is not None:
            if self._process_styles(styles_path, theme):
                changed = True

        # 3. Restart only if something actually changed.
        if changed and self.restart:
            self.reload()

    # ------------------------------------------------------------------
    # yasb_colors.css
    # ------------------------------------------------------------------

    def _render_colors(self, theme: Theme) -> str:
        lines: list[str] = [":root {"]
        for var, role in self.variables.items():
            if not theme.has(role):
                self.log_warn(
                    f"variable {var!r} maps to unknown role {role!r}"
                )
                continue
            value = theme.get(role)
            lines.append(f"    --{var}: {value};")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # styles.css
    # ------------------------------------------------------------------

    def _process_styles(self, path: Path, theme: Theme) -> bool:
        """Run both substitutions on styles.css. Returns True if the
        file was modified and written.
        """
        try:
            original = read_text(path)
        except OSError as e:
            self.log_warn(f"could not read {path}: {e}")
            return False

        # Lookup for hex substitution: config overrides state.
        state_map = self._load_state_map()
        config_map = {k.lower(): v for k, v in self.color_map.items()}
        lookup = {**state_map, **config_map}

        # Pass A: variable declarations.
        text, var_changes, var_written = self._substitute_vars(original, theme)

        # Pass B: hex literals. Variable writes are visible to this
        # pass so a hex we just wrote to a variable is not
        # re-classified as something else.
        hex_lookup = {**lookup, **var_written}
        text, hex_changes, hex_mappings = self._substitute_hexes(
            text, theme, hex_lookup
        )

        # Persist mappings even if the file didn't change. It records
        # what we *would* have written, useful on the next run.
        new_state = {**state_map, **var_written, **hex_mappings}
        self._save_state_map(new_state)

        if text == original:
            return False

        # Real change. Bump marker so yasb's watcher fires.
        marker = f"/* kamo-update: {int(time.time())} */"
        if _MARKER_RE.search(text):
            text = _MARKER_RE.sub(marker, text)
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = text + sep + marker + "\n"

        try:
            write_text(path, text)
        except OSError as e:
            self.log_warn(f"could not write {path}: {e}")
            return False

        self.log_info(
            f"updated styles.css ({var_changes} var, {hex_changes} hex)"
        )
        return True

    # ------------------------------------------------------------------
    # Variable substitution
    # ------------------------------------------------------------------

    def _substitute_vars(
        self, text: str, theme: Theme
    ) -> tuple[str, int, dict[str, str]]:
        """Replace the value of any `--name: value;` declaration where
        `name` is in self.variables. Returns (new_text, changes,
        written_hex_to_role).
        """
        changes = 0
        written: dict[str, str] = {}

        def repl(m: re.Match) -> str:
            nonlocal changes
            full = m.group(1)          # "--background: " (with spaces)
            name_match = re.match(r"--([\w-]+)", full)
            if not name_match:
                return m.group(0)
            var_name = name_match.group(1)
            if var_name not in self.variables:
                return m.group(0)
            role = self.variables[var_name]
            if not theme.has(role):
                return m.group(0)

            new_val = theme.get(role).lower()
            old_val = m.group(2).strip()

            # Reserved? Don't touch.
            if old_val.startswith("#"):
                base, _ = self._split_hex(old_val.lstrip("#"))
                if base and base in self.reserved_hexes:
                    return m.group(0)

            written[new_val] = role
            if old_val.lower() == new_val:
                return m.group(0)

            # Also record the original hex if it looks like one, so
            # future hex substitutions can find it if it appears
            # elsewhere in the file.
            if old_val.startswith("#"):
                base, _ = self._split_hex(old_val.lstrip("#"))
                if base:
                    written[base] = role

            changes += 1
            return m.group(1) + new_val + m.group(3)

        new_text = _VAR_DECL.sub(repl, text)
        return new_text, changes, written

    # ------------------------------------------------------------------
    # Hex substitution
    # ------------------------------------------------------------------

    def _substitute_hexes(
        self,
        text: str,
        theme: Theme,
        lookup: dict[str, str],
    ) -> tuple[str, int, dict[str, str]]:
        """Replace every hex token in `text`.

        Lookup order: `lookup` dict first, then auto-classify.
        Returns (new_text, changes, hex_to_role) where hex_to_role
        records every substitution made.
        """
        changes = 0
        recorded: dict[str, str] = {}

        def repl(m: re.Match) -> str:
            nonlocal changes
            raw = m.group(1)
            base, alpha = self._split_hex(raw)
            if base is None:
                return m.group(0)
            if base in self.reserved_hexes:
                return m.group(0)

            role = lookup.get(base)
            if role is None:
                role = self._classify_hex(base)
            if role is None or not theme.has(role):
                return m.group(0)

            new_base = theme.get(role).lstrip("#").lower()
            recorded[base] = role
            recorded[new_base] = role

            if new_base == base:
                return m.group(0)

            changes += 1
            if alpha:
                return "#" + new_base + alpha
            return "#" + new_base

        new_text = _HEX_TOKEN.sub(repl, text)
        return new_text, changes, recorded

    # ------------------------------------------------------------------
    # Hex parsing and classification
    # ------------------------------------------------------------------

    @staticmethod
    def _split_hex(raw: str) -> tuple[str | None, str | None]:
        """Split a raw hex string (no leading #) into (base_6digit,
        alpha_2digit_or_None). Returns (None, None) on invalid input.
        """
        n = len(raw)
        if n == 3:
            return "".join(c * 2 for c in raw).lower(), None
        if n == 4:
            base = "".join(c * 2 for c in raw[:3]).lower()
            alpha = (raw[3] * 2).lower()
            return base, alpha
        if n == 6:
            return raw.lower(), None
        if n == 8:
            return raw[:6].lower(), raw[6:].lower()
        return None, None

    def _classify_hex(self, base: str) -> str | None:
        """Best-guess role for a 6-digit lowercase hex. None if the
        hex can't be parsed or doesn't fit any band.
        """
        if not re.match(r"^[0-9a-f]{6}$", base):
            return None
        try:
            L, chroma, hue = C.hex_to_oklch("#" + base)
        except Exception:
            return None

        # Saturated colors: pick by hue.
        if chroma >= 0.08:
            best_role: str | None = None
            best_dist = 40.0
            for role, anchor in _HUE_ANCHORS.items():
                d = C.hue_distance(hue, anchor)
                if d < best_dist:
                    best_role = role
                    best_dist = d
            return best_role or "accent"

        # Neutrals: pick by lightness band. Thresholds are the
        # midpoints between consecutive targets in the theme's
        # neutral ramp.
        if L >= 0.875: return "text"
        if L >= 0.770: return "subtext1"
        if L >= 0.680: return "subtext0"
        if L >= 0.600: return "overlay2"
        if L >= 0.500: return "overlay1"
        if L >= 0.400: return "overlay0"
        if L >= 0.315: return "surface2"
        if L >= 0.250: return "surface1"
        if L >= 0.190: return "surface0"
        if L >= 0.145: return "base"
        if L >= 0.120: return "mantle"
        return "crust"

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _load_state_map(self) -> dict[str, str]:
        stored = state_mod.get(self.name, _STATE_KEY, {})
        if not isinstance(stored, dict):
            return {}
        return {
            k.lower(): v
            for k, v in stored.items()
            if isinstance(k, str) and isinstance(v, str)
        }

    def _save_state_map(self, m: dict[str, str]) -> None:
        state_mod.put(self.name, _STATE_KEY, m)