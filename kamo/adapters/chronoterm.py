"""Chronoterm adapter.

Chronoterm is a clock TUI. Its config is TOML:

    [colors]
    enabled  = true
    hours    = "#ff8200"
    minutes  = "#934f27"
    seconds  = "#683416"
    separator = "#683416"
    date     = "#e7d2ce"
    day      = "#e5a480"
    ampm     = "#a87054"
    timezone = "#a87054"
    border   = "#934f27"
    title    = "#cc8054"
    background = "reset"
    late_night_hours = "#c5682d"
    hour_palette = []

Kamo rewrites the string values inside [colors]. Everything else in
the file - including hour_palette, which is a list - is preserved.

Reload: chronoterm has no reload mechanism. To apply a new theme, the
running instance must be killed and relaunched. Kamo does this only
if a chronoterm process is actually running. If none is found, Kamo
just writes the config; the next time the user launches chronoterm,
the new colors are already there.

The process name and launch command are both configurable, because
how people launch a TUI varies a lot.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import Adapter, first_existing, read_text, resolve_path, write_text
from ..theme import Theme


# TOML key -> Theme role. `background` is intentionally omitted:
# "reset" is the correct value and replacing it would make the clock
# opaque over the terminal background.
DEFAULT_KEYS: dict[str, str] = {
    "hours":            "accent",
    "minutes":          "red",
    "seconds":          "surface2",
    "separator":        "surface2",
    "date":             "text",
    "day":              "subtext1",
    "ampm":             "subtext0",
    "timezone":         "subtext0",
    "border":           "surface1",
    "title":            "subtext1",
    "late_night_hours": "peach",
}

DEFAULT_CONFIG_CANDIDATES = [
    "~/.config/chronoterm/config.toml",
    "~/.chronoterm/config.toml",
    "~/.config/chrono-term/config.toml",
    "~/AppData/Roaming/chronoterm/config.toml",
    "~/AppData/Local/chronoterm/config.toml",
]

DEFAULT_PROCESS_NAME = "chronoterm.exe"
DEFAULT_LAUNCH_COMMAND = ["cmd", "/c", "start", "", "chronoterm"]

# Matches a TOML string assignment: `key = "value"` with optional
# surrounding whitespace. Single- or double-quoted.
_STRING_ASSIGN = (
    r'^(?P<indent>\s*)(?P<key>{key})\s*=\s*'
    r'(?P<quote>["\'])(?P<val>[^"\']*)(?P=quote)\s*$'
)


class ChronotermAdapter(Adapter):
    name = "chronoterm"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

        self.path = self._resolve_path()
        self.keys = self.cfg_dict("keys", DEFAULT_KEYS)
        self.process_name = self.cfg_value("process_name", DEFAULT_PROCESS_NAME)
        self.launch_command = self.cfg_value(
            "launch_command", DEFAULT_LAUNCH_COMMAND
        )
        self.kill_on_apply = bool(self.cfg_value("kill_on_apply", True))

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

        resolved: dict[str, str] = {}
        for key, role in self.keys.items():
            if not theme.has(role):
                self.log_warn(f"key {key!r} maps to unknown role {role!r}")
                continue
            resolved[key] = theme.get(role)

        if not resolved:
            self.log_warn("no valid keys; nothing to write")
            return

        new_text, changes = self._rewrite(original, resolved)

        if changes == 0:
            self.log_warn("no color keys found in [colors] section")
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

        if self.kill_on_apply:
            self._restart()

    # ------------------------------------------------------------------

    def _rewrite(self, text: str, resolved: dict[str, str]) -> tuple[str, int]:
        """Replace quoted string values inside the [colors] section."""
        lines = text.splitlines()
        out: list[str] = []
        in_colors = False
        changes = 0

        for line in lines:
            # Section boundary?
            m_section = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
            if m_section:
                in_colors = m_section.group(1).strip() == "colors"
                out.append(line)
                continue

            if not in_colors:
                out.append(line)
                continue

            # Try each key we care about.
            replaced = False
            for key, new_val in resolved.items():
                pattern = re.compile(
                    _STRING_ASSIGN.format(key=re.escape(key))
                )
                m = pattern.match(line)
                if m:
                    quote = m.group("quote")
                    indent = m.group("indent")
                    out.append(f"{indent}{key} = {quote}{new_val}{quote}")
                    changes += 1
                    replaced = True
                    break
            if not replaced:
                out.append(line)

        joined = "\n".join(out)
        if text.endswith("\n") and not joined.endswith("\n"):
            joined += "\n"
        return joined, changes

    # ------------------------------------------------------------------

    def _restart(self) -> None:
        running = self._is_running()
        if not running:
            self.log_info("chronoterm not running; config will apply on next launch")
            return

        self._kill()
        # Give the OS a moment to release any window or file lock.
        time.sleep(0.8)
        self._launch()

    def _is_running(self) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.process_name}",
                 "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return self.process_name.lower() in result.stdout.lower()

    def _kill(self) -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", self.process_name],
                capture_output=True,
                timeout=5,
            )
            self.log_info(f"killed {self.process_name}")
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log_warn(f"kill failed: {e}")

    def _launch(self) -> None:
        if not self.launch_command:
            self.log_warn("no launch_command configured; not relaunching")
            return
        try:
            subprocess.Popen(
                self.launch_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.log_info("relaunched chronoterm")
        except FileNotFoundError:
            self.log_warn(
                f"launch command {self.launch_command[0]!r} not found; "
                "launch chronoterm manually"
            )
        except OSError as e:
            self.log_warn(f"launch failed: {e}")