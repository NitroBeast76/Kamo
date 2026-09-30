"""Image -> Theme.

The only job: open an image, look at its colors, decide what each
Theme role should be, return a Theme. No I/O outside reading the
image. No side effects.

Approach:
    1. Quantize the image down to ~16 dominant colors with weights.
    2. Pick the primary accent: the most saturated candidate whose
       hue is within tolerance of the image's mood hue. Falls back to
       the most saturated overall if nothing is in-family. Done first
       so semantic roles can exclude it.
    3. Pick a "mood" neutral from the most prominent color - its hue
       tints every background and foreground, but chroma is clamped
       low so text stays readable.
    4. Build neutrals (backgrounds, surfaces, foregrounds) at fixed
       lightness targets, then push them toward WCAG contrast floors.
    5. Assign the 9 semantic/extras roles by nearest-hue matching
       against fixed anchors (red=25, green=145, etc.), excluding
       the accent. If nothing in the image is close to an anchor,
       synthesize a hue blended toward the mood hue so the accent
       stays in the wallpaper's family instead of anchoring to a
       fixed color. Otherwise, clamp the matched color into a usable
       accent band so dark wallpapers still produce visible
       highlights.
    6. Choose accent_text by whichever of black/white contrasts
       better on accent.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import color as C
from .theme import Theme


# Hue anchors for the semantic / extra accents. Order matters only
# for iteration; each role is scored independently.
HUE_ANCHORS: dict[str, float] = {
    "red":    25.0,
    "green":  145.0,
    "yellow": 85.0,
    "blue":   250.0,
    "mauve":  310.0,
    "teal":   180.0,
    "peach":  50.0,
    "pink":   350.0,
    "sky":    220.0,
}

# If the closest candidate for a role is more than this many degrees
# off its target hue, synthesize the color at the anchor instead of
# using a poor match. Keeps semantic roles recognizable on
# monochromatic wallpapers.
HUE_SYNTHESIS_THRESHOLD: float = 50.0

# How much a synthesized accent's hue is pulled toward the mood hue.
# 0 = synthesized colors keep their anchor hue (blue stays blue on a
# red wallpaper); 1 = they fully adopt the mood hue (everything
# becomes a shade of the wallpaper). 0.5 is a good middle: blue
# drifts to purple on a red wallpaper, green drifts to yellow-orange,
# and the whole palette reads as one family without collapsing into
# a single hue.
MOOD_BLEND: float = 0.5

# How far from the mood hue a candidate can be and still count as
# "in the wallpaper's color family" for the purpose of picking the
# primary accent. A purple wallpaper with one orange element will
# pick purple for its accent because the orange is 120 degrees away
# and the tolerance is 60.
MOOD_HUE_TOLERANCE: float = 60.0

# Chroma floor below which a color is considered "grey" rather than
# chromatic. Two uses:
#   - Candidates below this are skipped during accent assignment.
#     A grey with no hue shouldn't be assigned to a role whose whole
#     purpose is to carry a hue.
#   - If the dominant color is below this, the wallpaper is treated
#     as monochrome: synthesized accents use their raw anchors
#     instead of being blended toward a meaningless mood hue.
MONOCHROME_THRESHOLD: float = 0.03

# Usable accent band. Any color pulled from the image for a semantic
# or primary role gets clamped into this range, so highlights stay
# readable against a dark base and don't blow out against a light one.
ACCENT_MIN_L: float = 0.55
ACCENT_MAX_L: float = 0.85
ACCENT_MIN_C: float = 0.10

# Lightness targets for the neutral ramp. Chosen so that text/base,
# subtext/base, etc. have a fighting chance at passing contrast
# before the clamp kicks in.
NEUTRAL_TARGETS: dict[str, float] = {
    "base":     0.16,
    "mantle":   0.13,
    "crust":    0.11,
    "surface0": 0.22,
    "surface1": 0.28,
    "surface2": 0.35,
    "overlay0": 0.45,
    "overlay1": 0.55,
    "overlay2": 0.65,
    "text":     0.92,
    "subtext0": 0.72,
    "subtext1": 0.82,
}

# Contrast floors that apply after the neutral ramp is built.
CONTRAST_FLOORS = [
    ("text",     "base",     7.0),
    ("subtext1", "base",     5.5),
    ("subtext0", "base",     4.5),
    ("text",     "surface0", 6.0),
    ("subtext0", "surface0", 3.5),
]


# ---------------------------------------------------------------------
# Hue helpers
# ---------------------------------------------------------------------

def _blend_hue(a: float, b: float, t: float) -> float:
    """Interpolate from hue a to hue b along the shorter arc.

    t=0 returns a, t=1 returns b. Result in [0, 360).

    Example: blending 250 (blue) toward 27 (red) by 0.5 goes the
    short way around the wheel (through purple/magenta) rather than
    the long way through green, yellow, and orange.
    """
    diff = ((b - a + 180.0) % 360.0) - 180.0
    return (a + diff * t) % 360.0


# ---------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------

def _quantize(image_path: Path, n: int = 16, sample: int = 200
              ) -> list[tuple[str, float]]:
    """Return [(hex, weight), ...] sorted by weight desc.

    The image is thumbnailed to `sample` pixels on the long edge before
    quantizing. That is fast (a few ms) and produces a palette that
    reflects the whole image, not just one corner.
    """
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((sample, sample))
    quant = img.quantize(colors=n, method=Image.MEDIANCUT)
    palette = quant.getpalette()
    counts = sorted(quant.getcolors(), reverse=True)

    total = sum(c for c, _ in counts) or 1
    out: list[tuple[str, float]] = []
    for count, idx in counts:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        out.append((C.rgb_to_hex(r, g, b), count / total))
    return out


# ---------------------------------------------------------------------
# Accent assignment
# ---------------------------------------------------------------------

def _usable_accent(hex_c: str,
                   min_L: float = ACCENT_MIN_L,
                   max_L: float = ACCENT_MAX_L,
                   min_C: float = ACCENT_MIN_C) -> str:
    """Nudge a picked color into a band that reads as a usable accent.

    Hue is preserved. Lightness is clamped into [min_L, max_L] if
    outside. Chroma is raised to min_C if too low. Colors already
    inside the band are returned unchanged.

    This is what makes a dark wallpaper produce visible accents: a
    mauve that lives at L=0.24 in the image is technically correct,
    but against a base at L=0.16 it disappears. Clamping to L>=0.55
    keeps the hue but makes the role functional.
    """
    L, chroma, H = C.hex_to_oklch(hex_c)
    new_L = max(min_L, min(max_L, L))
    new_C = max(min_C, chroma)
    if new_L == L and new_C == chroma:
        return hex_c
    return C.oklch_to_hex(new_L, new_C, H)


def _assign_accents(candidates: list[tuple[str, float]],
                    mood_hue: float,
                    exclude: set[str] | None = None,
                    hue_threshold: float = HUE_SYNTHESIS_THRESHOLD,
                    mood_blend: float = MOOD_BLEND,
                    ) -> dict[str, str]:
    """Pick one source color per semantic role. Never reuses a color.

    Grey candidates (chroma below MONOCHROME_THRESHOLD) are skipped:
    a color with no meaningful chroma can't represent a hue, and
    assigning one to an accent role produces the wrong hex.

    When no candidate for a role is chromatic and close enough to
    the anchor hue, the role is synthesized at a hue partway between
    its anchor and the wallpaper's mood hue. The `mood_blend`
    parameter controls how far toward the mood: 0.0 disables
    blending entirely, which is what monochrome wallpapers use so
    their synthesized roles land on canonical anchors.

    If the synthesized value collides with one already assigned,
    the hue is nudged in small steps until it doesn't.
    """
    used: set[str] = set(exclude or ())
    assigned: dict[str, str] = {}

    _NUDGE_STEPS = (5.0, -5.0, 10.0, -10.0, 15.0, -15.0, 20.0, -20.0)

    for role, target_hue in HUE_ANCHORS.items():
        scored: list[tuple[float, float, str]] = []
        for hex_c, weight in candidates:
            if hex_c in used:
                continue
            _, chroma, hue = C.hex_to_oklch(hex_c)
            # Skip greys. Monochrome wallpapers end up with an empty
            # scored list and fall through to synthesis for every
            # role, which is the correct behavior.
            if chroma < MONOCHROME_THRESHOLD:
                continue
            hd = C.hue_distance(hue, target_hue)
            cost = 3.0 * (hd / 180.0) + 1.0 * abs(chroma - 0.15) - 0.3 * weight
            scored.append((cost, hd, hex_c))
        scored.sort(key=lambda x: x[0])

        result: str | None = None
        picked_raw: str | None = None

        for cost, hd, hex_c in scored:
            if hd > hue_threshold:
                break
            clamped = _usable_accent(hex_c)
            if clamped in used:
                continue
            result = clamped
            picked_raw = hex_c
            break

        if result is None:
            blended = _blend_hue(target_hue, mood_hue, mood_blend)
            result = C.oklch_to_hex(0.62, 0.13, blended)

            if result in used:
                for offset in _NUDGE_STEPS:
                    candidate = C.oklch_to_hex(
                        0.62, 0.13, (blended + offset) % 360.0
                    )
                    if candidate not in used:
                        result = candidate
                        break
        else:
            used.add(picked_raw)

        used.add(result)
        assigned[role] = result

    return assigned


def _pick_primary_accent(
    candidates: list[tuple[str, float]],
    mood_hue: float,
    hue_tolerance: float = MOOD_HUE_TOLERANCE,
) -> tuple[str | None, str]:
    """Pick the primary accent from the raw candidates.

    Only considers candidates with meaningful chroma — a grey
    wallpaper has no chromatic content, and treating its
    numerical-artifact "hue" as a real one produces arbitrary
    accents from the color wheel.

    Prefers the most saturated in-family candidate. If none is
    within tolerance of the mood hue, falls back to the most
    saturated chromatic candidate overall. If there are no
    chromatic candidates at all, returns (None, default blue).
    """
    chromatic: list[tuple[str, float]] = []
    for hex_c, _ in candidates:
        _, chroma, _ = C.hex_to_oklch(hex_c)
        if chroma < MONOCHROME_THRESHOLD:
            continue
        chromatic.append((hex_c, chroma))

    if not chromatic:
        return None, "#89b4fa"

    in_family: list[tuple[str, float]] = []
    for hex_c, chroma in chromatic:
        _, _, hue = C.hex_to_oklch(hex_c)
        if C.hue_distance(hue, mood_hue) <= hue_tolerance:
            in_family.append((hex_c, chroma))

    pool = in_family if in_family else chromatic

    best, best_chroma = None, -1.0
    for hex_c, chroma in pool:
        if chroma > best_chroma:
            best, best_chroma = hex_c, chroma

    if best is None:
        return None, "#89b4fa"
    return best, _usable_accent(best)


def _accent_text(accent: str) -> str:
    """Pick black or white for text on top of `accent`."""
    on_black = C.contrast("#000000", accent)
    on_white = C.contrast("#ffffff", accent)
    return "#1a1a1a" if on_black >= on_white else "#ffffff"


# ---------------------------------------------------------------------
# Neutral ramp
# ---------------------------------------------------------------------

def _neutral_ramp(mood_hue: float, mood_chroma: float) -> dict[str, str]:
    """Build the 12 neutral roles at their target lightnesses.

    Every neutral shares the mood hue but with very low chroma. As
    lightness approaches the extremes (base very dark, text very
    light), chroma is squashed further, because high chroma at extreme
    lightness reads as "tinted" rather than "dark".
    """
    # Cap the mood chroma. High enough that the wallpaper's hue is
    # unmistakable on every role — backgrounds, borders, text, all
    # of it. Low enough that the contrast floors still pass without
    # having to move lightness. 0.20 is a deliberate choice in favor
    # of visible mood over restraint.
    base_chroma = min(mood_chroma, 0.20)
    out: dict[str, str] = {}

    for role, L in NEUTRAL_TARGETS.items():
        # Fade chroma gently toward the extremes. Even at L=0.11
        # (crust) and L=0.92 (text) we keep most of the mood, because
        # those are the roles the user actually looks at. A red
        # wallpaper should give a red-brown base and a warm cream
        # text, not a grey base and a grey text with a hint of pink.
        span = 1.0 - abs(L - 0.5) * 0.6
        span = max(0.65, min(1.0, span))
        chroma = base_chroma * span
        out[role] = C.oklch_to_hex(L, chroma, mood_hue)

    return out


def _apply_contrast_floors(neutrals: dict[str, str]) -> dict[str, str]:
    """Push foregrounds away from backgrounds until contrast passes."""
    out = dict(neutrals)
    for fg_role, bg_role, target in CONTRAST_FLOORS:
        fg = out[fg_role]
        bg = out[bg_role]
        if C.contrast(fg, bg) < target:
            out[fg_role] = C.ensure_readable(fg, bg, target)
    return out


# ---------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------

def _assemble(neutrals: dict[str, str],
              accents: dict[str, str],
              accent: str,
              accent_text: str,
              ) -> Theme:
    """Build the Theme from resolved pieces. Shared by both entry
    points so the field mapping lives in exactly one place."""
    return Theme(
        base=neutrals["base"],
        mantle=neutrals["mantle"],
        crust=neutrals["crust"],
        surface0=neutrals["surface0"],
        surface1=neutrals["surface1"],
        surface2=neutrals["surface2"],
        overlay0=neutrals["overlay0"],
        overlay1=neutrals["overlay1"],
        overlay2=neutrals["overlay2"],
        text=neutrals["text"],
        subtext0=neutrals["subtext0"],
        subtext1=neutrals["subtext1"],
        accent=accent,
        accent_text=accent_text,
        red=accents["red"],
        green=accents["green"],
        yellow=accents["yellow"],
        blue=accents["blue"],
        mauve=accents["mauve"],
        teal=accents["teal"],
        peach=accents["peach"],
        pink=accents["pink"],
        sky=accents["sky"],
    )


def _from_candidates(candidates: list[tuple[str, float]]) -> Theme:
    """Shared pipeline: candidates -> Theme. Both public entry points
    end up here."""
    dominant_hex, _ = candidates[0]
    _, dominant_chroma, dominant_hue = C.hex_to_oklch(dominant_hex)

    # Monochrome wallpapers have a dominant hue that is a numerical
    # artifact of hex_to_oklch on grey (it always returns 89.9 for
    # pure greys). Blending synthesized accents toward that hue
    # produces arbitrary colors. Disable blending entirely so
    # synthesized accents land on their canonical anchors.
    monochrome = dominant_chroma < MONOCHROME_THRESHOLD

    neutrals = _neutral_ramp(dominant_hue, dominant_chroma)
    neutrals = _apply_contrast_floors(neutrals)

    accent_raw, accent = _pick_primary_accent(candidates, dominant_hue)
    exclude: set[str] = {accent}
    if accent_raw:
        exclude.add(accent_raw)

    blend = 0.0 if monochrome else MOOD_BLEND
    accents = _assign_accents(
        candidates, dominant_hue, exclude=exclude, mood_blend=blend,
    )
    accent_text = _accent_text(accent)

    return _assemble(neutrals, accents, accent, accent_text)


def build_theme(image_path: str | Path) -> Theme:
    """Read an image and return a Theme.

    Raises FileNotFoundError if the image is missing, or
    PIL.UnidentifiedImageError if it cannot be decoded. Callers
    should catch and log.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    candidates = _quantize(image_path, n=16)
    if not candidates:
        raise RuntimeError(f"no colors extracted from {image_path}")

    return _from_candidates(candidates)


def build_theme_from_hexes(hexes: list[str]) -> Theme:
    """Same as build_theme but from an explicit list of hex colors.

    Useful for tests and for feeding a pre-extracted palette (e.g.
    from pywal) without writing an image.
    """
    candidates: list[tuple[str, float]] = []
    n = len(hexes)
    for i, h in enumerate(hexes):
        # Weight linearly decreasing; order is assumed to be relevance.
        candidates.append((h, (n - i) / n))

    return _from_candidates(candidates)