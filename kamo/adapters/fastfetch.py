"""Fastfetch adapter.

Fastfetch reads its config on every invocation - there is no daemon,
no reload IPC, nothing to signal. Rewriting the file is the entire
job.

Two color surfaces in config.jsonc:

    1. The logo block: eight keys "2".."9" whose values are hex
       strings. These match $2..$9 placeholders in the ASCII art
       file next to the config.

    2. Inline module colors: `"color"`, `"keyColor"` fields scattered
       across modules, each a hex string.

Logo block: rewritten by *key name*. `"2"` through `"9"` are found
by pattern and set to the current gradient stops. Key-based, no
state needed.

Inline colors: rewritten by *hex value*. The inline_map says
"`#FF8200` should become the accent role". On the first apply this
works. On every apply after that, `#FF8200` is gone - replaced by
the previous theme's accent. Without memory, nothing matches and the
inline colors freeze.

So Kamo remembers what it wrote last time (in state.json) and adds
both the original source hexes and its own previous writes to the
substitution map. Every apply works, forever.

The config is JSONC (JSON with // comments), so we deliberately do
not parse it - a real parser would strip comments and reformat the
file.

Inline map values may also carry an alpha suffix:

    "#FF8200": "accent"           plain role
    "#FF8200": "base@0.20"        role with alpha, emitted as #AARRGGBB

Alpha support is minimal for now; add it if a config needs it.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from .. import state as state_mod
from ..theme import Theme


# Inline hex -> Theme role. Tuned for the current fastfetch config,
# which uses five distinct warm tones.
DEFAULT_INLINE_MAP: dict[str, str] = {
    "#FF8200": "accent",
    "#E5A480": "text",
    "#CC8054": "subtext1",
    "#A87054": "subtext0",
    "#934F27": "surface2",
}

# Lightness / chroma multipliers for the logo ramp. Index 0 is $2
# (darkest), index 7 is $9 (brightest). Same shape as cava's gradient
# so all apps render a wallpaper the same way.
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

# Matches "key": "value" for a single digit key, inside or outside a
# block. The color block is scoped separately.
_LOGO_ENTRY = re.compile(
    r'("(?P<key>[2-9])"\s*:\s*")(?P<hex>#[0-9A-Fa-f]{6})(")'
)

# Matches the first "color": { ... } block, non-greedy.
_COLOR_BLOCK = re.compile(
    r'("color"\s*:\s*\{)(.*?)(\})',
    re.DOTALL,
)

# Matches a bare hex token. Same pattern as Flow Launcher's, with the
# word-boundary guard against partial matches.
_HEX_TOKEN = re.compile(r"#[0-9A-Fa-f]{6}\b")

# State file key. The value stored under this key is a dict of
# {"role": "#hex"} - what Kamo wrote last time for each role.
_STATE_KEY = "inline"


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

        # Inline substitution. Uses state memory so every apply can
        # find the previous theme's hexes, not just the original ones.
        last_written = state_mod.get(self.name, _STATE_KEY, {}) or {}
        resolved_inline = self._resolve_inline(theme, last_written)
        if resolved_inline:
            source = self._substitute_inline(source, resolved_inline)

        # Logo gradient. Key-based, no state needed.
        gradient = self._render_gradient(theme)
        source, _ = self._substitute_logo(source, gradient)

        if source == original:
            self.log_info("config already up to date")
            return

        try:
            write_text(self.path, source)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        # Remember what we just wrote, keyed by role, so next apply
        # can find these hexes even though the originals are gone.
        self._remember_written(theme)

        self.log_info(f"wrote {self.path.name}")

    # ------------------------------------------------------------------

    def _resolve_inline(
        self,
        theme: Theme,
        last_written: dict[str, str],
    ) -> dict[str, str]:
        """Build the substitution map: source_hex -> new_hex.

        Two sources of source_hex for each role:

            1. The original hex from DEFAULT_INLINE_MAP. Matches a
               pristine config the user just restored.

            2. The hex Kamo wrote last time for the same role. This
               is the normal case on every apply after the first,
               because the original hexes have been overwritten.

        Both keys map to the current theme's value for that role.
        """
        out: dict[str, str] = {}
        for source_hex, role in self.inline_map.items():
            if not theme.has(role):
                self.log_warn(
                    f"inline {source_hex!r} maps to unknown role {role!r}"
                )
                continue
            new_val = theme.get(role)

            # Pristine-config match.
            out[source_hex.lower()] = new_val

            # Previous-write match.
            prev = last_written.get(role)
            if prev and prev.lower() != source_hex.lower():
                out[prev.lower()] = new_val

        return out

    def _remember_written(self, theme: Theme) -> None:
        """Persist what this apply just wrote for each role."""
        written: dict[str, str] = {}
        for _source_hex, role in self.inline_map.items():
            if theme.has(role):
                written[role] = theme.get(role)
        if written:
            state_mod.put(self.name, _STATE_KEY, written)

    def _render_gradient(self, theme: Theme) -> dict[str, str]:
        """Return {"2": "#...", ..., "9": "#..."} for the logo block."""
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

    # ------------------------------------------------------------------

    @staticmethod
    def _substitute_inline(source: str, mapping: dict[str, str]) -> str:
        def repl(m: re.Match) -> str:
            return mapping.get(m.group(0).lower(), m.group(0))
        return _HEX_TOKEN.sub(repl, source)

    def _substitute_logo(
        self, source: str, gradient: dict[str, str]
    ) -> tuple[str, int]:
        """Replace the values of keys "2".."9" inside the first
        "color" block. Other "color" blocks (module-level ones) are
        left alone because the sub runs on the scoped inner text.
        """
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