"""Fastfetch adapter.

Fastfetch reads its config on every invocation - there is no daemon,
no reload IPC, nothing to signal. Rewriting the file is the entire
job.

Two color surfaces in config.jsonc:

    1. The logo block: eight keys "2".."9" whose values are hex
       strings. These match $2..$9 placeholders in the ASCII art
       file next to the config. Handled by key, always rewritten
       from the current theme's accent gradient.

    2. Inline hex literals: every other `"#rrggbb"` in the file,
       whether under a `color` key, a `keyColor` key, or anywhere
       else. Handled by value substitution.

Inline hex substitution has three sources of truth, checked in
order:

    1. `inline_map` from kamo.toml. Explicit user pins. Highest
       priority, always wins.

    2. Previous write. state.json remembers every hex Kamo has
       written and the role it wrote it as. On the next apply,
       those hexes are found and re-swapped to the new theme.

    3. Auto-classification by OKLCH. Same logic as the yasb
       adapter: saturated colors by hue anchor, neutrals by
       lightness band. Picks the closest theme role.

Auto-classification is what makes Kamo work on any fastfetch
config without the user writing a map. The map exists only for the
case where auto gets a specific color wrong and the user wants to
pin it.

Alpha channel is preserved. 8-digit hexes are split into base +
alpha, the base is substituted, the alpha is reattached.

The config is JSONC (JSON with // comments), so we deliberately do
not parse it - a real parser would strip comments and reformat the
file.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from .. import state as state_mod
from ..theme import Theme


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

# User pins: source hex -> role. Empty by default; users override in
# kamo.toml if auto-classification gets a specific hex wrong.
DEFAULT_INLINE_MAP: dict[str, str] = {}

# Hexes Kamo must never touch. Stored without leading #, lowercase.
DEFAULT_RESERVED_HEXES: list[str] = []

# Lightness / chroma multipliers for the logo ramp. Index 0 is $2
# (darkest), index 7 is $9 (brightest).
LOGO_STOP_KEYS = ["2", "3", "4", "5", "6", "7", "8", "9"]
LOGO_STOP_SHAPE = [
    (0.30, 0.60),
    (0.42, 0.80),
    (0.55, 0.90),
    (0.62, 1.00),
    (0.70, 1.00),
    (0.72, 1.00),
    (0.82, 0.50),
    (0.92, 0.20),
]

DEFAULT_CONFIG_CANDIDATES = [
    "~/.config/fastfetch/config.jsonc",
    "~/.config/fastfetch/config.json",
    "~/AppData/Roaming/fastfetch/config.jsonc",
    "~/AppData/Local/fastfetch/config.jsonc",
]

# Hue anchors. Same values as palette.py and yasb.py so all three
# agree on what "red" means.
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

# Marker regex for `"2".."9"` inside a color block.
_LOGO_ENTRY = re.compile(
    r'("(?P<key>[2-9])"\s*:\s*")(?P<hex>#[0-9A-Fa-f]{6,8})(")'
)

# Any "color": { ... } block, non-greedy.
_COLOR_BLOCK = re.compile(
    r'("color"\s*:\s*\{)(.*?)(\})',
    re.DOTALL,
)

# Any hex token: 3, 4, 6, or 8 hex digits.
_HEX_TOKEN = re.compile(r"#([0-9A-Fa-f]{3,8})\b")

# State key for the hex->role map.
_STATE_KEY = "hex_map"


class FastfetchAdapter(Adapter):
    name = "fastfetch"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.path = self._resolve_path()
        self.inline_map = self.cfg_dict("inline_map", DEFAULT_INLINE_MAP)
        self.gradient_role = self.cfg_value("gradient_role", "accent")
        self.gradient_keys = list(
            self.cfg_value("gradient_keys", LOGO_STOP_KEYS)
        )
        self.reserved_hexes = {
            h.lstrip("#").lower()
            for h in self.cfg_value(
                "reserved_hexes", DEFAULT_RESERVED_HEXES
            )
        }

    # ------------------------------------------------------------------

    def _resolve_path(self) -> Path | None:
        override = self.cfg.get("config")
        if override:
            p = resolve_path(override)
            return p if p.exists() else None
        return first_existing(DEFAULT_CONFIG_CANDIDATES)

    def is_available(self) -> bool:
        return self.path is not None and self.path.exists()

    # ------------------------------------------------------------------

    def apply(self, theme: Theme) -> None:
        if self.path is None:
            self.log_warn("no config path resolved; skipping")
            return

        try:
            source = read_text(self.path)
        except OSError as e:
            self.log_error(f"could not read {self.path}: {e}")
            return

        original = source

        # Build substitution lookup. Order of precedence:
        #   user color_map > state memory > auto (auto runs during sub)
        state_map = self._load_state_map()
        config_map = {k.lower(): v for k, v in self.inline_map.items()}
        lookup = {**state_map, **config_map}

        # Pass 1: inline hexes everywhere in the file.
        source, inline_changes, recorded = self._substitute_hexes(
            source, theme, lookup
        )

        # Pass 2: logo gradient. Key-based; runs after so its keys
        # aren't caught by the generic hex sub.
        gradient = self._render_gradient(theme)
        source, _ = self._substitute_logo(source, gradient)

        # Persist what we substituted so the next apply can find these
        # hexes even though the originals are gone.
        new_state = {**state_map, **config_map, **recorded}
        self._save_state_map(new_state)

        if source == original:
            self.log_info("config already up to date")
            return

        try:
            write_text(self.path, source)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        self.log_info(f"wrote {self.path.name} ({inline_changes} inline)")

    # ------------------------------------------------------------------

    def _substitute_hexes(
        self,
        text: str,
        theme: Theme,
        lookup: dict[str, str],
    ) -> tuple[str, int, dict[str, str]]:
        """Replace every hex token. Returns (new_text, changes, recorded).
        `recorded` maps every source hex and its replacement back to
        the role they represent, for state persistence.
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

    @staticmethod
    def _split_hex(raw: str) -> tuple[str | None, str | None]:
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
        if not re.match(r"^[0-9a-f]{6}$", base):
            return None
        try:
            L, chroma, hue = C.hex_to_oklch("#" + base)
        except Exception:
            return None

        if chroma >= 0.08:
            best_role: str | None = None
            best_dist = 40.0
            for role, anchor in _HUE_ANCHORS.items():
                d = C.hue_distance(hue, anchor)
                if d < best_dist:
                    best_role = role
                    best_dist = d
            return best_role or "accent"

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
    # Logo gradient (unchanged behavior)
    # ------------------------------------------------------------------

    def _render_gradient(self, theme: Theme) -> dict[str, str]:
        role = (
            self.gradient_role
            if theme.has(self.gradient_role)
            else "accent"
        )
        accent = theme.get(role)
        _, acc_chroma, acc_hue = C.hex_to_oklch(accent)

        out: dict[str, str] = {}
        for key, (L, chroma_mult) in zip(self.gradient_keys, LOGO_STOP_SHAPE):
            chroma = acc_chroma * chroma_mult
            out[key] = C.oklch_to_hex(L, chroma, acc_hue)
        return out

    def _substitute_logo(
        self, source: str, gradient: dict[str, str]
    ) -> tuple[str, int]:
        match = _COLOR_BLOCK.search(source)
        if not match:
            return source, 0

        prefix, inner, suffix = match.group(1), match.group(2), match.group(3)
        replaced = [0]

        def entry_repl(m: re.Match) -> str:
            key = m.group("key")
            if key not in gradient:
                return m.group(0)
            new_hex = gradient[key]
            if m.group("hex").lower() == new_hex.lower():
                return m.group(0)
            replaced[0] += 1
            return f'{m.group(1)}{new_hex}"'

        new_inner = _LOGO_ENTRY.sub(entry_repl, inner)
        return (
            source[: match.start()]
            + prefix
            + new_inner
            + suffix
            + source[match.end():],
            replaced[0],
        )

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