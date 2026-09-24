"""yasb adapter.

yasb reads two files:

    styles.css       - layout, sizes, and often the color variables
                       themselves. Historically a mix of both.
    yasb_colors.css  - color variables, imported from styles.css.

Two common patterns exist:

    1. Pure import: styles.css has `@import "yasb_colors.css";` at
       the top and defines nothing itself. Kamo writing
       yasb_colors.css is enough.

    2. Hardcoded: styles.css has `@import` AND a `:root { ... }`
       block with hardcoded `--background`, `--accent`, etc. CSS
       cascade means the later hardcoded block wins and the import
       is ignored.

Kamo handles both. It writes yasb_colors.css (canonical location)
AND updates any `--var: value;` inside a `:root` block in
styles.css, so a hardcoded block gets overwritten with the new
theme's values.

The naming mismatch ("colors.css" vs "yasb_colors.css") is a common
footgun. The default here is yasb_colors.css because that is what a
fresh yasb install imports. If your styles.css imports something
else, override `colors_file` in kamo.toml.

Reload: two mechanisms, in order.

    1. yasb watches its stylesheet when `watch_stylesheet: true`. The
       watcher fires on content changes, not mtime, so Kamo writes a
       marker comment at the bottom of styles.css on every content
       change.

    2. If restart = true (the default) and yasb.exe is running, Kamo
       kills and relaunches it. Belt-and-suspenders: if the watcher
       misses the change or is disabled, the restart catches it.

Both reload paths are gated on whether anything actually changed.
If the theme produces identical files, no write, no marker bump,
no restart.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from ..theme import Theme


# Marker comment rewritten on content changes to force yasb's file
# watcher to fire. Must be a content change, not just an mtime change.
_MARKER_RE = re.compile(r"/\*\s*kamo-update:\s*\d+\s*\*/")

# Any ":root { ... }" block. Body is everything between the braces.
_ROOT_BLOCK = re.compile(r"(:root\s*\{)([^}]*)(\})", re.DOTALL)


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

        changed = False

        # 1. Write yasb_colors.css, skipping if identical.
        css = self._render_colors(theme)
        existing = ""
        try:
            existing = read_text(colors_path)
        except OSError:
            pass

        if css != existing:
            try:
                write_text(colors_path, css)
                changed = True
                self.log_info(f"wrote {colors_path.name}")
            except OSError as e:
                self.log_error(f"could not write {colors_path}: {e}")
        else:
            self.log_info(f"{colors_path.name} already up to date")

        # 2. Update any hardcoded :root blocks in styles.css.
        styles_path = self._styles_path()
        if styles_path is not None:
            if self._update_styles(styles_path, theme):
                changed = True

        # 3. Restart only if something actually changed.
        if changed and self.restart:
            self.reload()

    # ------------------------------------------------------------------

    def _render_colors(self, theme: Theme) -> str:
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

    def _update_styles(self, path: Path, theme: Theme) -> bool:
        """Update hardcoded color variables inside :root blocks in
        styles.css. Returns True if the file was actually modified.

        Only the declared variables are touched. Font declarations,
        border radii, and any unrelated CSS is left alone. Comments
        inside the block are preserved (the regex matches `--var: x;`
        sequences, not whole lines).
        """
        try:
            text = read_text(path)
        except OSError as e:
            self.log_warn(f"could not read {path}: {e}")
            return False

        # Precompute var -> new value from this theme.
        new_values: dict[str, str] = {}
        for var, role in self.variables.items():
            if theme.has(role):
                new_values[var] = theme.get(role)

        if not new_values:
            return False

        def repl_block(m: re.Match) -> str:
            opening, body, closing = m.groups()
            for var, new_val in new_values.items():
                body = re.sub(
                    rf"(--{re.escape(var)}\s*:\s*)([^;\r\n]+)(\s*;)",
                    rf"\g<1>{new_val}\g<3>",
                    body,
                )
            return opening + body + closing

        new_text = _ROOT_BLOCK.sub(repl_block, text)

        if new_text == text:
            # Nothing in :root changed. No write, no marker bump.
            return False

        # Body changed. Bump the marker comment so yasb's watcher fires.
        marker = f"/* kamo-update: {int(time.time())} */"
        if _MARKER_RE.search(new_text):
            new_text = _MARKER_RE.sub(marker, new_text)
        else:
            sep = "" if new_text.endswith("\n") else "\n"
            new_text = new_text + sep + marker + "\n"

        try:
            write_text(path, new_text)
            self.log_info("updated :root variables in styles.css")
            return True
        except OSError as e:
            self.log_warn(f"could not write {path}: {e}")
            return False