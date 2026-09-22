"""yasb adapter.

yasb reads two files:

    styles.css       - layout, sizes, everything that isn't a color.
                       Starts with `@import "yasb_colors.css";`
    yasb_colors.css  - the color variables. This file is what Kamo
                       writes. styles.css is never touched.

The naming mismatch ("colors.css" vs "yasb_colors.css") is a common
footgun. The default here is yasb_colors.css because that is what a
fresh yasb install imports. If your styles.css imports something
else, override `colors_file` in kamo.toml.

Reload: yasb watches its stylesheet when `watch_stylesheet: true` is
set in its config. We do not touch that setting; if the user has it
off, they get stale colors until they restart yasb. We do touch
styles.css's mtime as a nudge - some builds only fire the watcher
when the *importing* file changes, not the imported one.
"""

from __future__ import annotations

from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from ..theme import Theme


# CSS variable -> Theme role. This mirrors the variable names yasb's
# default styles.css consumes.
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

# Candidate locations for the yasb config directory.
DEFAULT_DIR_CANDIDATES = [
    "~/.config/yasb",
    "~/AppData/Roaming/yasb",
    "~/AppData/Local/yasb",
]


class YasbAdapter(Adapter):
    name = "yasb"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.dir = self._resolve_dir()
        self.colors_file = self.cfg_value("colors_file", "yasb_colors.css")
        self.styles_file = self.cfg_value("styles_file", "styles.css")
        self.variables = self.cfg_dict("variables", DEFAULT_VARIABLES)

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
        # The colors file may not exist yet; yasb tolerates it as long
        # as styles.css imports something. We only require the styles
        # file as evidence yasb is actually installed here.
        return self._styles_path() is not None

    # ------------------------------------------------------------------

    def apply(self, theme: Theme) -> None:
        colors_path = self._colors_path()
        if colors_path is None:
            self.log_warn("no config directory resolved; skipping")
            return

        css = self._render(theme)

        try:
            write_text(colors_path, css)
        except OSError as e:
            self.log_error(f"could not write {colors_path}: {e}")
            return

        self.log_info(f"wrote {colors_path.name}")

        styles_path = self._styles_path()
        if styles_path is not None:
            self._touch(styles_path)

    # ------------------------------------------------------------------

    def _render(self, theme: Theme) -> str:
        lines: list[str] = [":root {"]
        for var, role in self.variables.items():
            if not theme.has(role):
                self.log_warn(f"variable {var!r} maps to unknown role {role!r}")
                continue
            value = theme.get(role)
            lines.append(f"    --{var}: {value};")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _touch(path: Path) -> None:
        """Bump mtime so yasb's stylesheet watcher fires."""
        try:
            import os
            os.utime(path, None)
        except OSError:
            pass