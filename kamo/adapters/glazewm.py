"""GlazeWM adapter.

GlazeWM stores its config as YAML. Colors appear in exactly two
places in the default schema:

    window_effects:
      focused_window:
        border:
          color: '#ff8200'
      other_windows:
        border:
          color: '#683416'

Kamo writes those two hex values in place, preserving comments,
formatting, and every other line of the file.

Reload: GlazeWM exposes `glazewm command wm-reload-config`. If the
CLI isn't on PATH, the file is still written; the user reloads
manually with alt+shift+r.

Why not parse the whole YAML? Because GlazeWM configs are hand-edited
and full of comments. Loading + dumping with PyYAML would strip every
comment and reflow the file. Line-targeted replacement is boring and
safe.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from ..theme import Theme


# YAML anchor -> Theme role. The anchors are the top-level keys under
# window_effects.
DEFAULT_ANCHORS: dict[str, str] = {
    "focused_window": "accent",
    "other_windows": "surface2",
}

DEFAULT_CONFIG_CANDIDATES = [
    "~/.glzr/glazewm/config.yaml",
    "~/.glzr/glazewm/config.yml",
    "~/AppData/Roaming/glzr/glazewm/config.yaml",
    "~/AppData/Local/glzr/glazewm/config.yaml",
]

DEFAULT_RELOAD_COMMAND = ["glazewm", "command", "wm-reload-config"]

# Matches a YAML color line: `color: '#rrggbb'` or `color: "#rrggbb"`,
# with arbitrary leading indentation.
_COLOR_LINE = re.compile(
    r"^(?P<indent>\s*)color:\s*(?P<quote>['\"])(?P<hex>#[0-9A-Fa-f]{6})(?P=quote)\s*$"
)


class GlazewmAdapter(Adapter):
    name = "glazewm"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.path = self._resolve_path()
        self.anchors = self.cfg_dict("anchors", DEFAULT_ANCHORS)
        self.reload_command = self.cfg_value(
            "reload_command", DEFAULT_RELOAD_COMMAND
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
            original = read_text(self.path)
        except OSError as e:
            self.log_error(f"could not read {self.path}: {e}")
            return

        # Resolve roles -> hex once, so a typo in the anchor map is
        # reported once instead of per-line.
        resolved: dict[str, str] = {}
        for anchor, role in self.anchors.items():
            if not theme.has(role):
                self.log_warn(f"anchor {anchor!r} maps to unknown role {role!r}")
                continue
            resolved[anchor] = theme.get(role)

        if not resolved:
            self.log_warn("no valid anchors; nothing to write")
            return

        new_text, changes = self._rewrite(original, resolved)

        if changes == 0:
            self.log_warn("no border color lines found; config schema changed?")
            return

        if new_text == original:
            self.log_info("colors already up to date")
            return

        try:
            write_text(self.path, new_text)
        except OSError as e:
            self.log_error(f"could not write {self.path}: {e}")
            return

        self.log_info(f"updated {changes} color(s) in {self.path.name}")
        self._reload()

    # ------------------------------------------------------------------

    def _rewrite(self, text: str, resolved: dict[str, str]) -> tuple[str, int]:
        """Replace `color:` lines under each tracked anchor.

        Tracks state by indentation, not by "last key seen":

        - `window_effects:` enters the region. Its indent is remembered.
        - The first key one level deeper is the "child indent", where
            `focused_window` and `other_windows` sit.
        - Keys at exactly child indent set the current anchor.
        - Keys at deeper indent (like `border:` inside `focused_window`)
            are nested and do NOT change the current anchor.
        - Any key at window_effects' indent or above exits the region.

        Without this, `border:` would clear the anchor and the `color:`
        line inside it would never be replaced.
        """
        lines = text.splitlines()
        out: list[str] = []
        we_indent = -1          # -1 = not inside window_effects
        child_indent = -1       # not yet known
        current_anchor: str | None = None
        replaced: set[str] = set()
        changes = 0

        for line in lines:
            # Structural lines: "key:", "key: value", "key:" nested.
            m_key = re.match(r"^(\s*)([A-Za-z_][\w-]*):", line)

            if m_key:
                indent = len(m_key.group(1))
                key = m_key.group(2)

                # Entering window_effects.
                if key == "window_effects" and indent == len(line) - len(line.lstrip()):
                    # Only treat it as the section header if the line
                    # has no value after the colon.
                    if re.match(r"^\s*window_effects:\s*$", line):
                        we_indent = indent
                        child_indent = -1
                        current_anchor = None
                        out.append(line)
                        continue

                # Exiting window_effects (top-level key at same or
                # lower indent).
                if we_indent >= 0 and indent <= we_indent:
                    we_indent = -1
                    child_indent = -1
                    current_anchor = None
                    out.append(line)
                    continue

                # Inside window_effects: anchor selection by indent.
                if we_indent >= 0:
                    if child_indent < 0:
                        child_indent = indent
                    if indent == child_indent:
                        current_anchor = key if key in resolved else None

            # Color line in a tracked anchor.
            if current_anchor and current_anchor not in replaced:
                m_color = _COLOR_LINE.match(line)
                if m_color:
                    new_hex = resolved[current_anchor]
                    quote = m_color.group("quote")
                    indent_str = m_color.group("indent")
                    out.append(f"{indent_str}color: {quote}{new_hex}{quote}")
                    replaced.add(current_anchor)
                    changes += 1
                    continue

            out.append(line)

        joined = "\n".join(out)
        if text.endswith("\n") and not joined.endswith("\n"):
            joined += "\n"
        return joined, changes

    # ------------------------------------------------------------------

    def _reload(self) -> None:
        if not self.reload_command:
            return
        try:
            result = subprocess.run(
                self.reload_command,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            self.log_warn(
                f"{self.reload_command[0]!r} not on PATH; reload manually"
            )
            return
        except subprocess.TimeoutExpired:
            self.log_warn("reload command timed out")
            return
        except OSError as e:
            self.log_warn(f"reload failed: {e}")
            return

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            self.log_warn(f"reload returned {result.returncode}: {err}")