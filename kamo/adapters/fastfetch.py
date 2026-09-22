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

Both are rewritten by text substitution. The config is JSONC
(JSON with // comments), so we deliberately do not parse it - a
real parser would strip comments and reformat the file.

The inline map is checked first: any hex token present in it is
replaced. Then the logo block is found and its keys "2".."9" are
overwritten. This ordering means a hex value that appears both as a
logo stop and inline (unlikely but possible) is handled by the
inline map, and the logo substitution uses the pattern-scoped
version, so neither leaks into the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
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

        # Resolve inline map once.
        resolved_inline = self._resolve_inline(theme)
        if resolved_inline:
            source = self._substitute_inline(source, resolved_inline)

        # Rewrite the logo gradient block.
        gradient = self._render_gradient(theme)
        source, replaced = self._substitute_logo(source, gradient)

        if source == original:
            self.log_info("config already up to date")
            return

        try:
            write_text(self.path, source)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        self.log_info(f"wrote {self.path.name}")

    # ------------------------------------------------------------------

    def _resolve_inline(self, theme: Theme) -> dict[str, str]:
        out: dict[str, str] = {}
        for source_hex, role in self.inline_map.items():
            if not theme.has(role):
                self.log_warn(f"inline {source_hex!r} maps to unknown role {role!r}")
                continue
            out[source_hex.lower()] = theme.get(role)
        return out

    def _render_gradient(self, theme: Theme) -> dict[str, str]:
        """Return {"2": "#...", ..., "9": "#..."} for the logo block."""
        role = self.gradient_role if theme.has(self.gradient_role) else "accent"
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

        return source[: match.start()] + prefix + new_inner + suffix + source[match.end():], replaced[0]