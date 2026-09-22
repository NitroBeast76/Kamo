"""Cava adapter.

Cava's config is a hand-written INI variant:

    [general]
    live-config = 1

    [color]
    background = '#000000'
    foreground = '#e7d2ce'
    gradient = 1
    gradient_color_1 = '#7a1a1a'
    ...
    gradient_color_8 = '#ffffff'

Two things Kamo does:

    1. Ensures `live-config = 1` is set in [general], so cava picks
       up future changes without a restart. Only done once; if the
       user has explicitly set it to 0, we do not fight them.
    2. Rewrites the [color] section with a fresh 8-stop gradient
       interpolated from the theme's accent in OKLCH.

Reload: cava has no reload IPC. With live-config = 1, it watches its
own file. Otherwise, the user restarts it manually. Kamo does not
kill cava - it lives inside a terminal, and killing it is disruptive
in a way that killing a system tray app is not.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from .. import color as C
from ..theme import Theme


DEFAULT_CONFIG_CANDIDATES = [
    "~/.config/cava/config",
    "~/.config/cava/config.toml",
    "~/AppData/Local/cava/config",
]

# The [color] block we write. Stops go dark -> bright, hue from accent.
GRADIENT_STOPS = 8

# Lightness and chroma multipliers for each stop, index 0..7.
# Chroma is scaled by the accent's chroma so a grey accent produces a
# grey ramp and a vivid one produces a vivid ramp.
_GRADIENT_SHAPE = [
    (0.25, 0.45),
    (0.35, 0.70),
    (0.50, 0.90),
    (0.65, 1.00),
    (0.75, 1.00),
    (0.82, 0.60),
    (0.88, 0.30),
    (0.95, 0.00),
]


class CavaAdapter(Adapter):
    name = "cava"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.path = self._resolve_path()
        self.set_live_config = bool(self.cfg_value("live_config", True))
        self.background_role = self.cfg_value("background_role", "base")
        self.foreground_role = self.cfg_value("foreground_role", "text")
        self.gradient_role = self.cfg_value("gradient_role", "accent")

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
            text = read_text(self.path)
        except OSError as e:
            self.log_error(f"could not read {self.path}: {e}")
            return

        original = text

        if self.set_live_config:
            text = self._ensure_live_config(text)

        text = self._replace_color_section(text, theme)

        if text == original:
            self.log_info("config already up to date")
            return

        try:
            write_text(self.path, text)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        self.log_info(f"wrote {self.path.name}")

    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_live_config(text: str) -> str:
        """Set live-config = 1 in [general] if present and not already 1.

        Only touches an existing key or adds it right under [general].
        If [general] is missing entirely, does nothing - the config
        is unexpected enough that poking it would be rude.
        """
        if re.search(r"^\s*\[general\]\s*$", text, flags=re.MULTILINE) is None:
            return text

        # Already set to 1?
        if re.search(r"^\s*live-config\s*=\s*1\s*$", text, flags=re.MULTILINE):
            return text

        # Replace existing live-config (commented or not).
        pattern = re.compile(r"^(\s*;?\s*)live-config\s*=\s*\d+\s*$", re.MULTILINE)
        if pattern.search(text):
            return pattern.sub("live-config = 1", text, count=1)

        # Add it right after [general].
        return re.sub(
            r"^(\s*\[general\]\s*)$",
            r"\1\nlive-config = 1",
            text,
            count=1,
            flags=re.MULTILINE,
        )

    # ------------------------------------------------------------------

    def _replace_color_section(self, text: str, theme: Theme) -> str:
        block = self._render_color_block(theme)

        # Match from [color] to the next section header or EOF.
        pattern = re.compile(
            r"^\[color\][^\[]*?(?=^\[|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        if pattern.search(text):
            return pattern.sub(block, text, count=1)

        # No [color] section; append one at the end.
        sep = "" if text.endswith("\n") else "\n"
        return text + sep + "\n" + block

    def _render_color_block(self, theme: Theme) -> str:
        bg = theme.get(self.background_role) if theme.has(self.background_role) else theme.base
        fg = theme.get(self.foreground_role) if theme.has(self.foreground_role) else theme.text

        accent_role = self.gradient_role if theme.has(self.gradient_role) else "accent"
        accent = theme.get(accent_role)
        _, acc_chroma, acc_hue = C.hex_to_oklch(accent)

        stops = []
        for i, (L, chroma_mult) in enumerate(_GRADIENT_SHAPE):
            chroma = acc_chroma * chroma_mult
            stops.append(C.oklch_to_hex(L, chroma, acc_hue))

        lines = [
            "[color]",
            "",
            f"background = '{bg}'",
            f"foreground = '{fg}'",
            "",
            "gradient = 1",
            "",
        ]
        for i, s in enumerate(stops):
            lines.append(f"gradient_color_{i + 1} = '{s}'")
        lines.append("")
        return "\n".join(lines)