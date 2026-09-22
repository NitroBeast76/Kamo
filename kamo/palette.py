"""Image -> Theme.

The only job: open an image, look at its colors, decide what each
Theme role should be, return a Theme. No I/O outside reading the
image. No side effects.

Approach:
    1. Quantize the image down to ~16 dominant colors with weights.
    2. Pick the primary accent: the most saturated candidate. Done
       first so semantic roles can exclude it.
    3. Pick a "mood" neutral from the most prominent color - its hue
       tints every background and foreground, but chroma is clamped
       low so text stays readable.
    4. Build neutrals (backgrounds, surfaces, foregrounds) at fixed
       lightness targets, then push them toward WCAG contrast floors.
    5. Assign the 9 semantic/extras roles by nearest-hue matching
       against fixed anchors (red=25, green=145, etc.), excluding
       the accent. If nothing in the image is close to an anchor,
       synthesize from the anchor so semantic roles stay semantic.
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

def _assign_accents(candidates: list[tuple[str, float]],
                    exclude: set[str] | None = None,
                    hue_threshold: float = HUE_SYNTHESIS_THRESHOLD,
                    ) -> dict[str, str]:
    """Pick one source color per semantic role. Never reuses a color.

    `exclude` is a set of hexes already assigned elsewhere (typically
    just the primary accent). Colors in it are skipped.

    If the closest available candidate is more than `hue_threshold`
    degrees from the role's target hue, synthesize at the anchor
    instead. This keeps red red, green green, etc. even when the
    image has only one hue.
    """
    used: set[str] = set(exclude or ())
    assigned: dict[str, str] = {}

    for role, target_hue in HUE_ANCHORS.items():
        best: str | None = None
        best_cost = float("inf")
        best_hue_dist = 999.0

        for hex_c, weight in candidates:
            if hex_c in used:
                continue
            _, chroma, hue = C.hex_to_oklch(hex_c)
            hd = C.hue_distance(hue, target_hue)
            cost = 3.0 * (hd / 180.0) + 1.0 * abs(chroma - 0.15) - 0.3 * weight
            if cost < best_cost:
                best = hex_c
                best_cost = cost
                best_hue_dist = hd

        if best is None or best_hue_dist > hue_threshold:
            # Nothing close enough in the image. Synthesize at the
            # anchor so the role is visually recognizable.
            best = C.oklch_to_hex(0.62, 0.13, target_hue)

        used.add(best)
        assigned[role] = best

    return assigned


def _pick_primary_accent(candidates: list[tuple[str, float]]) -> str:
    """Pick the primary accent from the raw candidates.

    Called before semantic roles are assigned, so no color is used
    twice. The most saturated candidate wins; if the image is grey,
    fall back to the built-in default.
    """
    best, best_chroma = None, -1.0
    for hex_c, _ in candidates:
        _, chroma, _ = C.hex_to_oklch(hex_c)
        if chroma > best_chroma:
            best, best_chroma = hex_c, chroma
    return best or "#89b4fa"


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
    # Cap the mood chroma. A vivid wallpaper should tint the neutrals,
    # not recolor them.
    base_chroma = min(mood_chroma, 0.04)
    out: dict[str, str] = {}

    for role, L in NEUTRAL_TARGETS.items():
        # Fade chroma toward the extremes: full at L=0.5, ~30% at
        # L=0.1 or L=0.95.
        span = 1.0 - abs(L - 0.5) * 1.4
        span = max(0.3, min(1.0, span))
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

    neutrals = _neutral_ramp(dominant_hue, dominant_chroma)
    neutrals = _apply_contrast_floors(neutrals)

    # Accent first so semantic roles can exclude it.
    accent = _pick_primary_accent(candidates)
    accents = _assign_accents(candidates, exclude={accent})
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