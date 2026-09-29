`--resync` works. Now the README.

```markdown
<div align="center">
  <img src="assets/kamo.png" alt="Kamo" width="160">
  <h1>Kamo</h1>
  <p><em>Kamo is shy, so it tries to blend in with the wallpaper.</em></p>
</div>

---

Kamo is a dynamic wallpaper engine for Windows. It watches your
wallpaper — static or live — and when it changes, it recolors every
app you use to match. Bar, terminal, launcher, clock, music player,
window borders: all of it shifts to the wallpaper's palette, in a few
seconds, without you touching a config file.

It runs in the system tray. It has no window, no settings dialog, no
first-run wizard. It reads your wallpaper, decides what the colors
should be, writes them into the apps you already use, and then gets
out of the way.

---

## What it does

1. Watches the current wallpaper (registry for static, screen grab
   for live).
2. Waits until it's been stable for a few seconds — no thrashing
   while you browse a gallery or drag a Lively scene around.
3. Extracts a color palette from the image.
4. Assigns that palette to named roles: `base`, `text`, `accent`,
   `red`, `green`, and so on.
5. Writes those roles into every supported app's config file.
6. Tells the app to reload, or notes that it will pick up on next
   launch.

That's the whole program. There's no theme marketplace, no cloud
sync, no account. It's about fifty kilobytes of logic wrapped around
the parts that are genuinely hard: perceptual color math and not
breaking people's dotfiles.

---

## Supported apps

| App | What gets themed | Reload |
|---|---|---|
| **yasb** | bar background, text, accents, borders, hover states | restart |
| **GlazeWM** | focused and unfocused window borders | CLI reload |
| **Cava** | background, foreground, 8-stop gradient | auto if `live-config=1` |
| **Chronoterm** | clock digits, date, border, title | restart |
| **Flow Launcher** | query box, results, selection, scrollbar | restart (opt-in) |
| **Fastfetch** | logo gradient + module key colors | none (re-read each run) |
| **Windows Terminal** | full ANSI scheme (20 slots) | automatic |

More apps are one file each. See **Writing an adapter** below.

---

## Install

### Option 1 — the .exe

Download `Kamo.exe`, drop it anywhere, run it. First launch writes
`%USERPROFILE%\.config\kamo\kamo.toml` and starts watching.

Add a shortcut to `shell:startup` to launch it at login. Or use the
tray menu's **Run at login** toggle, which writes the registry entry
for you.

### Option 2 — from source

```powershell
git clone https://github.com/NitroBeast76/Kamo
cd Kamo
pip install -e ".[dev]"
python -m kamo
```

Requires Python 3.10 or newer.

---

## Configuration

Kamo writes a commented config on first run. Open it from the tray
menu (**Open config folder**) or directly:

```
%USERPROFILE%\.config\kamo\kamo.toml
```

Every key is optional. Delete a line and the built-in default takes
over. Delete the whole file and it's regenerated.

The full template — the same one Kamo writes on first run:

```toml
# Kamo configuration.
#
# Auto-generated on first run. Every key below is optional - delete a
# line and the built-in default takes over. Delete the whole file and
# it will be regenerated with these values.
#
# Restart Kamo after editing.

[general]
poll_interval = 5.0     # seconds between wallpaper checks
settle_delay  = 8.0     # seconds of stability before applying
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
# restart        = true
# process_name   = "chronoterm.exe"
# launch_command = ["cmd", "/c", "start", "", "chronoterm"]
# config         = "%APPDATA%/chronoterm/config.toml"
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
#   brightRed = "red+0.10",
#   brightGreen = "green+0.10",
#   brightYellow = "yellow+0.10",
#   brightBlue = "blue+0.10",
#   brightPurple = "mauve+0.10",
#   brightCyan = "teal+0.10",
#   brightWhite = "text",
# }
```

### Common tweaks

**Slower settle, fewer applies.** If you flip through wallpapers a
lot, raise `settle_delay` to 15 or 30 seconds. Nothing applies until
you stop changing.

**Faster response.** Lower `settle_delay` to 5. Applies almost the
moment the wallpaper settles. Not recommended if you use a slideshow
or a live wallpaper engine.

**Disable an app.** Set `enabled = false` in its section.

**Different yasb variable names.** If your `styles.css` uses `--bg`
and `--fg` instead of `--background` and `--text`, override the
`variables` map:

```toml
[adapters.yasb.variables]
background = "base"
text       = "text"
accent     = "accent"
```

Anything you don't list falls back to the default.

**App restart behavior.** Some apps need to be killed and relaunched
to pick up new config. Each adapter that supports it has a `restart`
flag. Defaults are `true` for yasb, GlazeWM, and chronoterm; `false`
for Flow Launcher (killing it mid-query is rude). Set to `false`
anywhere you'd rather the change apply on next launch.

---

## How the colors are chosen

Kamo does not average pixels or run k-means. It works in **OKLCH**, a
perceptual color space, and it thinks in terms of roles rather than
colors.

**Step 1 — extract.** The image is thumbnailed to 200px and quantized
to 16 colors. That gives a small set of dominant colors with weights.

**Step 2 — mood.** The most prominent color supplies a hue. Every
neutral in the palette (backgrounds, surfaces, text) inherits that
hue, at low chroma. A red wallpaper gives you warm dark greys, not
dark reds. That's what keeps text readable on every image.

**Step 3 — the neutral ramp.** Backgrounds, surfaces, and foregrounds
are generated at fixed lightness targets. Chroma tapers toward the
extremes so pure black and pure white stay neutral. The chroma cap is
high enough to make the wallpaper's hue visible on a bar or a
terminal background, low enough to keep contrast.

**Step 4 — contrast floors.** Text must clear 7:1 against the
background, subtext 4.5:1. If the ramp fails, lightness moves away
from the background until it passes or runs out of room.

**Step 5 — accents.** Each accent role (`red`, `green`, `blue`, …)
has a fixed hue anchor. Kamo scores every candidate against every
anchor by hue distance, chroma, and prominence, then assigns the
best match. No source color is reused.

**Step 6 — synthesis with mood blending.** If a wallpaper has no
candidate close enough to an anchor, Kamo synthesizes the role at a
hue that's partway between the anchor and the mood hue. A red
wallpaper's "blue" lands on a warm purple; its "green" lands on a
yellow-orange. The result stays in the wallpaper's color family
instead of anchoring to a fixed hue that looks out of place.

**Step 7 — primary accent.** The most saturated candidate whose hue
is within 60° of the mood hue wins. A purple wallpaper with one
bright orange element gets a purple accent, not an orange one. If
nothing in the image is close, the most saturated overall is used.

The result: any wallpaper produces a palette that looks like it came
from that wallpaper, but never fails contrast. A pastel image and a
pitch-black image both yield readable UI.

---

## Running modes

```
Kamo.exe                 Start the tray app (default).
Kamo.exe --once IMG      Print the palette Kamo would extract from IMG.
Kamo.exe --resync        Apply the current wallpaper once and exit.
Kamo.exe --version       Print version and exit.
```

`--once` is the useful one. Point it at any image and it prints every
role with its hex and OKLCH coordinates. Useful for checking whether
Kamo picked the color you expected before letting it write anything.

```
$ Kamo.exe --once wallpaper.jpg
base          #14161c   L=0.16 C=0.018 H=250.3
mantle        #101218   L=0.13 C=0.014 H=250.3
crust         #0d0f14   L=0.11 C=0.013 H=250.3
...
accent        #7aa2f7   L=0.65 C=0.121 H=254.7
accent_text   #1a1a1a
red           #e06c75   L=0.62 C=0.148 H=21.0
green         #98c379   L=0.71 C=0.113 H=142.1
...
```

`--resync` applies the current wallpaper once and exits. It bypasses
the settle timer, so it finishes immediately. Useful from a script or
when you've edited a config by hand and want to see the effect.

---

## The tray menu

| Item | What it does |
|---|---|
| **Status** | "Ready (applied 12s ago)", "Pending…", "Applying…", "Paused", or "Error" |
| **Pause** | Stop reacting to wallpaper changes until you resume |
| **Apply now** | Skip the remaining settle window if a change is queued |
| **Resync** | Re-extract the current wallpaper and apply immediately |
| **Run at login** | Toggle a registry entry that launches Kamo at login |
| **Open config folder** | Opens `~/.config/kamo/` in Explorer |
| **Open log** | Opens `~/.config/kamo/kamo.log` |
| **Quit** | Exit |

The tray icon reflects the current accent color. When an apply fails,
it flips to red and the title shows the error.

---

## Logs

Everything goes to `%USERPROFILE%\.config\kamo\kamo.log`. The file
rotates at 512 KiB; one generation of history is kept as
`kamo.log.1`.

Every adapter logs its own progress under its name:

```
[2024-11-08 14:32:01] INFO  engine started (poll=5.0s, settle=8.0s)
[2024-11-08 14:32:01] INFO  active adapters: ['yasb', 'glazewm', 'cava', ...]
[2024-11-08 14:45:12] INFO  wallpaper changed (file), settling 8s
[2024-11-08 14:45:20] INFO  extracting theme from C:\...\wallpaper.jpg
[2024-11-08 14:45:20] INFO  [yasb] wrote yasb_colors.css
[2024-11-08 14:45:21] INFO  [glazewm] updated 2 color(s) in config.yaml
[2024-11-08 14:45:21] INFO  [cava] wrote config
[2024-11-08 14:45:21] WARN  [chronoterm] chronoterm not running; config applies on next launch
[2024-11-08 14:45:21] INFO  [flowlauncher] wrote Kamo.xaml
[2024-11-08 14:45:21] INFO  [fastfetch] wrote config.jsonc (17 inline)
[2024-11-08 14:45:22] INFO  [windowsterminal] updated 20 color(s) in scheme 'Interstellar'
```

When something goes wrong, the log names the adapter and the reason.
One adapter failing never stops the others.

---

## Known issues

**yasb v2.0.7 may ignore live stylesheet changes.** Kamo writes
`~/.config/yasb/styles.css` correctly — you can verify by opening the
file — but the running yasb process may not pick up the change, even
after being killed and relaunched. If your bar doesn't recolor:

1. Check the log for `[yasb] updated styles.css (N var, M hex)` with
   a nonzero count. If N and M are both zero, Kamo didn't write
   anything (unrelated bug).
2. Open `~/.config/yasb/styles.css` and confirm the `:root` block
   contains the theme's hexes. If it does, the file is correct.
3. Restart yasb manually (`Stop-Process -Name yasb; yasb`). If the
   bar still doesn't update, the app is caching the parsed stylesheet
   internally. That's a yasb-side issue, not Kamo's. Set
   `enabled = false` for yasb in `kamo.toml` and switch to a bar
   that reads its stylesheet on every change.

**Concurrent applies.** The engine guards against two applies running
at once via an in-progress flag. If you see
`apply already in progress; skipping duplicate` in the log, that's
the guard doing its job — not an error.

---

## Supported wallpaper engines

For static wallpapers, Kamo reads the registry key that Windows
itself uses — no polling, no guesswork.

For live wallpapers, there is no OS event when a frame changes. Kamo
detects a running live-wallpaper process and falls back to a
low-resolution screen grab. It's fast enough to run every five
seconds and precise enough to notice a wallpaper swap. Known engines:

- Lively Wallpaper
- Wallpaper Engine
- Anything running as `wallpaper32.exe` / `wallpaper64.exe`

If your engine isn't detected, open the log and check the process
name. Adding it to `watcher.py`'s `LIVE_PROCESS_NAMES` set is one
line.

---

## Writing an adapter

Each app Kamo supports has its own small file in
`kamo/adapters/`. An adapter answers two questions:

```python
class MyAppAdapter(Adapter):
    name = "myapp"

    def is_available(self) -> bool:
        # "Is this app installed on this machine?"
        return Path("~/.config/myapp/config").expanduser().exists()

    def apply(self, theme: Theme) -> None:
        # "Translate `theme` into this app's format and write it."
        ...
```

To add an app:

1. Create `kamo/adapters/myapp.py`.
2. Subclass `Adapter`.
3. Import it in `kamo/adapters/__init__.py` and add it to `REGISTRY`.
4. Add a section for it in `DEFAULT_TOML` (in `config.py`) so users
   know what's overridable.

That's the whole process. The watcher, palette builder, settle timer,
and tray don't change.

If you're adding an adapter for an app other people use, open a PR —
the code is small enough to review in one sitting.

---

## Building the .exe

```powershell
pip install -e ".[dev]"
pyinstaller `
  --onefile `
  --noconsole `
  --name Kamo `
  --icon kamo.ico `
  --add-data "kamo.ico;." `
  --hidden-import=pystray._win32 `
  --hidden-import=mss.windows `
  --hidden-import=psutil `
  --hidden-import=PIL._tkinter_finder `
  kamo\__main__.py
```

Output: `dist\Kamo.exe`. Roughly 30 MB with all dependencies.

For faster cold start, replace `--onefile` with `--onedir` and ship
the resulting folder. `--onefile` unpacks itself to a temp directory
on every launch, which costs 1–3 seconds. `--onedir` starts instantly.

---

## Design notes

A few decisions that look odd until you've used the tool.

**Why OKLCH?** Because RGB distance is a lie. Two colors thirty
percent apart in sRGB can be ten percent or fifty percent apart to
the eye, depending on hue. OKLCH is perceptually uniform: equal
numeric steps look like equal visual steps. Every brighten, darken,
and contrast check in Kamo happens in OKLCH.

**Why roles instead of color names?** Apps don't agree on what "red"
means. yasb wants `--redFlash`. GlazeWM wants a hex for "focused
border". Cava wants eight gradient stops. The only thing that
generalizes is a role set: `base`, `text`, `accent`, `red`. Each
adapter translates roles into its app's vocabulary.

**Why not parse configs?** Because every one of them is hand-edited
and full of comments. Loading a YAML or JSONC file through a parser
and re-dumping it erases all of that. Kamo does surgical text
substitution where possible, real parsing only where the format is
strict (Windows Terminal's JSON).

**Why auto-classify hexes instead of requiring a color map?** Because
writing a map for every hex in every config file is tedious and
error-prone. Kamo classifies each hardcoded hex by its OKLCH position
— hue for saturated colors, lightness for neutrals — and remembers
what it wrote in `state.json` so subsequent applies can find those
hexes even though the originals are gone. The `color_map` config
override exists for the case where auto-classification gets one
wrong, but most users never touch it.

**Why a settle timer?** Because wallpapers change constantly when
you're browsing a gallery, dragging a Lively scene around, or waiting
for a slideshow. Applying on every intermediate frame would thrash
every app on the system. Eight seconds of quiet is the default:
long enough to filter slideshows, short enough to feel responsive
when you deliberately change the wallpaper.

**Why does the tray icon change color?** So you can tell at a glance
that Kamo is alive, that it applied something, and which accent it
chose. It's the only visible output the app has.

---

## Requirements

- Windows 10 or 11
- Python 3.10+ (only if running from source)

Python packages (installed automatically by `pip install -e .`, or
bundled into the exe):

- `pillow` — image loading and quantization
- `pystray` — system tray
- `mss` — screen grab for live wallpapers
- `psutil` — process detection for live wallpapers
- `tomli` — TOML parsing on Python 3.10 only

Everything else is standard library.

---

## License

MIT. See `LICENSE`.
```

## What changed from your version

1. **Intro** — "in about twenty seconds" → "in a few seconds"
2. **Config template** — `settle_delay = 20.0` → `8.0`; added `restart` and `launch_command` lines to each adapter; fixed `scheme_name = "Interstellar"` → `"auto"`
3. **Common tweaks** — "raise to 30 or 45" → "15 or 30"; added "Faster response" and "App restart behavior" entries
4. **How the colors are chosen** — added Step 6 (mood blending) and renumbered; expanded Step 3 with the chroma cap explanation
5. **Log sample** — `settle=20.0s` → `settle=8.0s`; `wallpaper changed ... settling 20s` → `settling 8s`
6. **Known issues section** — new. Documents the yasb v2.0.7 issue and how to diagnose it. Documents the concurrent-apply guard log message.
7. **Build command** — added `--add-data "kamo.ico;."` (needed for the runtime icon to work in the exe)
8. **Design notes** — added "Why auto-classify hexes" note; updated the settle-timer paragraph from "twenty seconds" to "eight seconds"

Save it, then:

```powershell
git add README.md
git commit -m "README: reflect current defaults and behavior"
git push
