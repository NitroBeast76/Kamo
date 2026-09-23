"""Configuration loading for Kamo.

Two sources, merged:

    1. DEFAULTS          - hardcoded here, always present
    2. ~/.config/kamo/kamo.toml - user overrides, optional

On first run, if no user config exists, DEFAULT_TOML is written there
so the user has something to edit. It is fully commented and every
value matches DEFAULTS, so deleting it changes nothing.

Load order per field:
    user toml > DEFAULTS
There is no third layer here; adapters do their own auto-detection
after config is loaded.

A corrupt user config logs a warning and is ignored - Kamo always
starts. Missing values always fall back to DEFAULTS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

from . import log


CONFIG_DIR = Path.home() / ".config" / "kamo"
CONFIG_PATH = CONFIG_DIR / "kamo.toml"


# ---------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "general": {
        "poll_interval": 5.0,   # seconds between wallpaper checks
        "settle_delay": 20.0,   # seconds of stability before applying
        "log_level": "info",    # debug | info | warn | error
    },
    "adapters": {
        "yasb":            {"enabled": True, "restart": True},
        "glazewm":         {"enabled": True, "restart": True},
        "cava":            {"enabled": True},
        "chronoterm":      {"enabled": True, "restart": True},
        "flowlauncher":    {"enabled": True, "restart": False},
        "fastfetch":       {"enabled": True},
        "windowsterminal": {"enabled": True},
    },
}


# ---------------------------------------------------------------------
# Template written on first run
# ---------------------------------------------------------------------

DEFAULT_TOML = '''\
# Kamo configuration.
#
# Auto-generated on first run. Every key below is optional - delete a
# line and the built-in default takes over. Delete the whole file and
# it will be regenerated with these values.
#
# Restart Kamo after editing.

[general]
poll_interval = 5.0     # seconds between wallpaper checks
settle_delay  = 20.0    # seconds of stability before applying
log_level     = "info"  # debug | info | warn | error

# ---------------------------------------------------------------------
# Adapters
#
# Each adapter has its own section. Set enabled = false to skip it.
# Paths and role maps below are the defaults Kamo uses when a key is
# absent. Uncomment and edit only what you need to override.
# ---------------------------------------------------------------------

[adapters.yasb]
enabled = true
# restart        = true          # kill + relaunch yasb if running
# launch_command = ["yasb"]
# dir          = "~/.config/yasb"
# colors_file  = "yasb_colors.css"
# styles_file  = "styles.css"
# variables = {                  # CSS variable -> Theme role
#   background  = "base",
#   background2 = "surface0",
#   accent      = "accent",
#   accentText  = "accent_text",
#   text        = "text",
#   subtext     = "subtext0",
#   hover       = "surface1",
#   mutedBG     = "mantle",
#   border      = "surface2",
#   redFlash    = "red",
# }

[adapters.glazewm]
enabled = true
# restart        = true          # kill + relaunch if CLI reload fails
# launch_command = ["glazewm"]
# config         = "~/.glzr/glazewm/config.yaml"
# reload_command = ["glazewm", "command", "wm-reload-config"]
# anchors = {                    # YAML anchor -> Theme role
#   focused_window = "accent",
#   other_windows  = "surface2",
# }

[adapters.cava]
enabled = true
# config          = "~/.config/cava/config"
# live_config     = true         # sets live-config = 1 in cava config
# gradient_stops  = 8
# background_role = "base"
# foreground_role = "text"
# gradient_role   = "accent"     # palette source for gradient ramp

[adapters.chronoterm]
enabled = true
# config         = "~/.config/chronoterm/config.toml"
# process_name   = "chronoterm.exe"
# launch_command = ["cmd", "/c", "start", "", "chronoterm"]
# keys = {                       # TOML key -> Theme role
#   hours            = "accent",
#   minutes          = "red",
#   seconds          = "surface2",
#   separator        = "surface2",
#   date             = "text",
#   day              = "subtext1",
#   ampm             = "subtext0",
#   timezone         = "subtext0",
#   border           = "surface1",
#   title            = "subtext1",
#   late_night_hours = "peach",
# }

[adapters.flowlauncher]
enabled = true
# restart        = false         # off by default; restart interrupts typing
# launch_command = ["Flow.Launcher"]
# settings     = "%APPDATA%/FlowLauncher/Settings/Settings.json"
# themes_dir   = "%APPDATA%/FlowLauncher/Themes"
# base_theme   = "CircleDarkBlur.xaml"
# output_theme = "Kamo.xaml"
# theme_name   = "Kamo"
# color_map = {                  # source hex -> Theme role
#   "#ffffff" = "text",
#   "#a3a3a3" = "subtext1",
#   "#8a876e" = "subtext0",
#   "#bfbfbf" = "subtext0",
#   "#242424" = "surface0",
#   "#aeaeae" = "accent",
#   "#a0a8b5" = "overlay1",
#   "#9da1aa" = "subtext0",
#   "#a8abb3" = "subtext1",
#   "#373737" = "surface1",
#   "#c5d1da" = "subtext1",
# }

[adapters.fastfetch]
enabled = true
# config          = "~/.config/fastfetch/config.jsonc"
# gradient_keys   = ["2", "3", "4", "5", "6", "7", "8", "9"]
# gradient_role   = "accent"
# inline_map = {                 # source hex -> Theme role
#   "#FF8200" = "accent",
#   "#E5A480" = "text",
#   "#CC8054" = "subtext1",
#   "#A87054" = "subtext0",
#   "#934F27" = "surface2",
# }

[adapters.windowsterminal]
enabled = true
# settings    = "auto"
# scheme_name = "auto"           # "auto" = use the currently active scheme
# ansi_map = {                   # WT scheme key -> Theme role
#   background = "base",
#   foreground = "text",
#   cursorColor = "accent",
#   selectionBackground = "surface1",
#   black = "surface0",
#   red = "red",
#   green = "green",
#   yellow = "yellow",
#   blue = "blue",
#   purple = "mauve",
#   cyan = "teal",
#   white = "subtext1",
#   brightBlack = "overlay0",
#   brightRed = "red",
#   brightGreen = "green",
#   brightYellow = "yellow",
#   brightBlue = "blue",
#   brightPurple = "mauve",
#   brightCyan = "teal",
#   brightWhite = "text",
# }
'''


# ---------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base`. Override wins on leaf
    conflicts; nested dicts merge key-by-key. Neither input is mutated.
    """
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def ensure_file() -> bool:
    """Write DEFAULT_TOML if no config exists.

    Returns True if a new file was created, False if one already
    existed. Never raises on the "created" path; on failure to write,
    logs and returns False so Kamo keeps running with defaults.
    """
    if CONFIG_PATH.exists():
        return False
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(DEFAULT_TOML, encoding="utf-8")
        return True
    except OSError as e:
        log.warn(f"could not write default config: {e}")
        return False


def load() -> dict:
    """Return the merged config as a plain dict.

    Side effects:
      - ensures ~/.config/kamo/kamo.toml exists (writes defaults on
        first run)
      - sets the log level via log.init()

    Never raises. A missing or corrupt user config degrades to
    DEFAULTS with a warning.
    """
    created = ensure_file()

    if created:
        log.info(f"wrote default config to {CONFIG_PATH}")

    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            user = tomllib.loads(raw)
        except Exception as e:
            log.warn(f"config parse failed ({e}); using defaults")
            user = {}

    merged = _deep_merge(DEFAULTS, user)

    level = merged.get("general", {}).get("log_level", "info")
    log.init(level)

    return merged


# ---------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------

def get_general(cfg: dict) -> dict:
    return cfg.get("general", DEFAULTS["general"])


def get_adapter(cfg: dict, name: str) -> dict:
    """Return the config slice for one adapter, merged with defaults.

    Always includes at least {"enabled": <bool>}.
    """
    default = DEFAULTS["adapters"].get(name, {"enabled": True})
    user = cfg.get("adapters", {}).get(name, {})
    return _deep_merge(default, user)


def is_enabled(cfg: dict, name: str) -> bool:
    return bool(get_adapter(cfg, name).get("enabled", True))