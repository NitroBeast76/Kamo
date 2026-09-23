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

Reload: two mechanisms, in order.

    1. yasb watches its stylesheet when `watch_stylesheet: true`. But
    the watcher fires on content changes, not mtime, so `os.utime`
       alone does nothing. Kamo rewrites a marker comment at the
       bottom of styles.css. That fires the watcher.

    2. If restart = true (the default) and yasb.exe is running, Kamo
       kills and relaunches it. Belt-and-suspenders: if the watcher
       misses the change or is disabled, the restart catches it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from ..theme import Theme


# Marker comment rewritten on every apply to force yasb's file
# watcher to fire. Must be a content change, not just an mtime change.
_MARKER_RE = re.compile(r"/\*\s*kamo-update:\s*\d+\s*\*/")


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

    process_name = "yasb.exe"
    launch_command = ["yasb"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.dir = self._resolve_dir()
        self.colors_file = self.cfg_value("colors_file", "yasb_colors.css")
        self.styles_file = self.cfg_value("styles_file", "styles.css")
        self.variables = self.cfg_dict("variables", DEFAULT_VARIABLES)

        self.restart = bool(self.cfg_value("restart", True))
        if not self.restart:
            # Disables reload() by clearing the process name.
            self.process_name = None

        # Allow overriding launch_command from config.
        cmd = self.cfg_value("launch_command", None)
        if isinstance(cmd, list) and cmd:
            self.launch_command = cmd
    
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
            self._bump_styles(styles_path)

        if self.restart:
            self.reload()
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

    def _bump_styles(self, path: Path) -> None:
        """Rewrite a marker comment in styles.css so yasb's watcher
        fires. os.utime only changes mtime, which does not trigger
        Qt's QFileSystemWatcher. Only a content change does.
        """
        try:
            text = read_text(path)
        except OSError as e:
            self.log_warn(f"could not read styles.css for reload: {e}")
            return

        marker = f"/* kamo-update: {int(time.time())} */"
        if _MARKER_RE.search(text):
            new_text = _MARKER_RE.sub(marker, text)
        else:
            sep = "" if text.endswith("\n") else "\n"
            new_text = text + sep + marker + "\n"

        if new_text == text:
            return

        try:
            write_text(path, new_text)
        except OSError as e:
            self.log_warn(f"could not write styles.css for reload: {e}")