"""OKLCH color math for Kamo.

Pure functions, no I/O, no state, no imports outside the stdlib.
Every other module in Kamo that touches color goes through here.

Coordinates:
    L  lightness, 0.0 (black) .. 1.0 (white)
    C  chroma, 0.0 (grey) .. ~0.37 (saturated, gamut-dependent)
    H  hue, 0.0 .. 360.0 degrees

All hex inputs accept "#rrggbb", "#rgb", "rrggbb" or "rgb", any case.
All hex outputs are lowercase "#rrggbb".
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------
# sRGB transfer functions
# ---------------------------------------------------------------------

def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


# ---------------------------------------------------------------------
# Hex <-> RGB
# ---------------------------------------------------------------------

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        raise ValueError(f"bad hex color: {h!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    r = max(0, min(255, round(r)))
    g = max(0, min(255, round(g)))
    b = max(0, min(255, round(b)))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------
# Hex <-> OKLCH
# ---------------------------------------------------------------------

def hex_to_oklch(h: str) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in hex_to_rgb(h))
    r = _srgb_to_linear(r)
    g = _srgb_to_linear(g)
    b = _srgb_to_linear(b)

    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    l_ = math.copysign(abs(l) ** (1 / 3), l)
    m_ = math.copysign(abs(m) ** (1 / 3), m)
    s_ = math.copysign(abs(s) ** (1 / 3), s)

    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

    C = math.hypot(a, bb)
    H = math.degrees(math.atan2(bb, a)) % 360.0
    return L, C, H


def _oklch_to_linear_rgb(L: float, C: float, H: float) -> tuple[float, float, float]:
    """OKLCH -> linear sRGB. May return values outside [0, 1]."""
    a = C * math.cos(math.radians(H))
    b = C * math.sin(math.radians(H))

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l = l_ ** 3
    m = m_ ** 3
    s = s_ ** 3

    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, bb


def _in_gamut(r: float, g: float, b: float, eps: float = 1e-6) -> bool:
    return (
        -eps <= r <= 1.0 + eps
        and -eps <= g <= 1.0 + eps
        and -eps <= b <= 1.0 + eps
    )


def _gamut_map(L: float, C: float, H: float) -> tuple[float, float, float]:
    """Return linear RGB for the given OKLCH, reducing chroma if
    necessary to stay inside the sRGB gamut. Lightness and hue are
    preserved; only chroma is sacrificed.

    Bisection on C. At C=0 the color is a pure grey at L, which is
    always in gamut for L in [0, 1]. 20 iterations converge well past
    the precision of an 8-bit channel.
    """
    r, g, b = _oklch_to_linear_rgb(L, C, H)
    if _in_gamut(r, g, b):
        return r, g, b

    lo, hi = 0.0, C
    for _ in range(20):
        mid = (lo + hi) * 0.5
        r, g, b = _oklch_to_linear_rgb(L, mid, H)
        if _in_gamut(r, g, b):
            lo = mid
        else:
            hi = mid
    return _oklch_to_linear_rgb(L, lo, H)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    L = max(0.0, min(1.0, L))
    C = max(0.0, C)

    r, g, b = _gamut_map(L, C, H)

    # After mapping, values are within rounding of [0, 1]; clamp for
    # safety before converting out of linear space.
    r = max(0.0, min(1.0, r))
    g = max(0.0, min(1.0, g))
    b = max(0.0, min(1.0, b))

    r = _linear_to_srgb(r)
    g = _linear_to_srgb(g)
    b = _linear_to_srgb(b)

    return rgb_to_hex(r * 255, g * 255, b * 255)


# ---------------------------------------------------------------------
# Hue helpers
# ---------------------------------------------------------------------

def hue_distance(a: float, b: float) -> float:
    """Shortest angular distance between two hues, in degrees (0..180)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ---------------------------------------------------------------------
# Luminance and contrast (WCAG 2.1)
# ---------------------------------------------------------------------

def relative_luminance(hex_color: str) -> float:
    r, g, b = (_srgb_to_linear(v / 255) for v in hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) .. 21.0 (black on white)."""
    la = relative_luminance(a)
    lb = relative_luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------
# Derived colors
# ---------------------------------------------------------------------

def brighten(hex_color: str, dL: float = 0.10, dC: float = 0.0) -> str:
    L, C, H = hex_to_oklch(hex_color)
    return oklch_to_hex(min(L + dL, 1.0), max(C + dC, 0.0), H)


def darken(hex_color: str, dL: float = 0.10) -> str:
    L, C, H = hex_to_oklch(hex_color)
    return oklch_to_hex(max(L - dL, 0.0), C, H)


def with_alpha(hex_color: str, alpha: float) -> str:
    """Return "#AARRGGBB" (WPF / XAML style). Alpha clamped to 0..1."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        raise ValueError(f"bad hex color: {h!r}")
    a = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"#{a:02X}{h.upper()}"


def ensure_readable(fg: str, bg: str, target: float = 4.5) -> str:
    """If fg on bg is below `target`, push fg's lightness away from bg
    until it passes (or we run out of room). Returns a hex."""
    if contrast(fg, bg) >= target:
        return fg

    L_fg, C_fg, H_fg = hex_to_oklch(fg)
    L_bg, _, _ = hex_to_oklch(bg)

    # Decide which direction to move: away from bg.
    going_up = L_bg < 0.5
    step = 0.02
    L = L_fg
    for _ in range(40):
        L = L + step if going_up else L - step
        if L <= 0.0 or L >= 1.0:
            break
        candidate = oklch_to_hex(L, C_fg, H_fg)
        if contrast(candidate, bg) >= target:
            return candidate
    # Ran out of room; return the extreme.
    return oklch_to_hex(1.0 if going_up else 0.0, C_fg, H_fg)