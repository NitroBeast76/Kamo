"""Tests for kamo.color.

Pure math, so no fixtures needed. Every test is a plain assert.
"""

from __future__ import annotations

import math

import pytest

from kamo import color as C


# ---------------------------------------------------------------------
# hex <-> rgb
# ---------------------------------------------------------------------

def test_hex_to_rgb_basic():
    assert C.hex_to_rgb("#ff0000") == (255, 0, 0)
    assert C.hex_to_rgb("#00ff00") == (0, 255, 0)
    assert C.hex_to_rgb("#0000ff") == (0, 0, 255)
    assert C.hex_to_rgb("#000000") == (0, 0, 0)
    assert C.hex_to_rgb("#ffffff") == (255, 255, 255)


def test_hex_to_rgb_short_form():
    assert C.hex_to_rgb("#f00") == (255, 0, 0)
    assert C.hex_to_rgb("#abc") == (170, 187, 204)


def test_hex_to_rgb_no_hash():
    assert C.hex_to_rgb("ff0000") == (255, 0, 0)


def test_hex_to_rgb_case_insensitive():
    assert C.hex_to_rgb("#FF0000") == (255, 0, 0)
    assert C.hex_to_rgb("#ff0000") == (255, 0, 0)


def test_hex_to_rgb_bad_input():
    with pytest.raises(ValueError):
        C.hex_to_rgb("#12345")
    with pytest.raises(ValueError):
        C.hex_to_rgb("")


def test_rgb_to_hex_roundtrip():
    for h in ["#000000", "#ffffff", "#89b4fa", "#f38ba8", "#a6e3a1"]:
        r, g, b = C.hex_to_rgb(h)
        assert C.rgb_to_hex(r, g, b) == h


def test_rgb_to_hex_clamps():
    assert C.rgb_to_hex(-10, 300, 128) == "#00ff80"


# ---------------------------------------------------------------------
# OKLCH
# ---------------------------------------------------------------------

def test_oklch_white_black():
    L, _, _ = C.hex_to_oklch("#ffffff")
    assert math.isclose(L, 1.0, abs_tol=0.001)
    L, _, _ = C.hex_to_oklch("#000000")
    assert math.isclose(L, 0.0, abs_tol=0.001)


def test_oklch_grey_has_zero_chroma():
    for h in ["#808080", "#404040", "#c0c0c0"]:
        _, chroma, _ = C.hex_to_oklch(h)
        assert chroma < 0.005


def test_oklch_hue_red_is_near_zero():
    _, _, H = C.hex_to_oklch("#ff0000")
    # Pure red sits around hue 29 in OKLCH, not 0. Just check it's
    # in the red sector.
    assert H < 60 or H > 330


def test_oklch_roundtrip():
    """Converting to OKLCH and back should give the same hex."""
    for h in ["#1e1e2e", "#89b4fa", "#f38ba8", "#a6e3a1", "#f9e2af"]:
        L, ch, H = C.hex_to_oklch(h)
        out = C.oklch_to_hex(L, ch, H)
        # Allow a tolerance of 1 unit per channel for clipping.
        r1, g1, b1 = C.hex_to_rgb(h)
        r2, g2, b2 = C.hex_to_rgb(out)
        assert abs(r1 - r2) <= 1
        assert abs(g1 - g2) <= 1
        assert abs(b1 - b2) <= 1


def test_oklch_to_hex_clamps_out_of_gamut():
    """Absurd chroma should not crash; result should be valid hex."""
    out = C.oklch_to_hex(0.5, 1.0, 200.0)
    r, g, b = C.hex_to_rgb(out)
    assert 0 <= r <= 255
    assert 0 <= g <= 255
    assert 0 <= b <= 255


# ---------------------------------------------------------------------
# hue
# ---------------------------------------------------------------------

def test_hue_distance_simple():
    assert C.hue_distance(0, 90) == 90
    assert C.hue_distance(0, 180) == 180
    assert C.hue_distance(0, 270) == 90  # shorter way around
    assert C.hue_distance(0, 359) == 1


def test_hue_distance_symmetric():
    assert C.hue_distance(30, 200) == C.hue_distance(200, 30)


# ---------------------------------------------------------------------
# contrast
# ---------------------------------------------------------------------

def test_contrast_extremes():
    assert math.isclose(C.contrast("#000000", "#ffffff"), 21.0, abs_tol=0.01)
    assert math.isclose(C.contrast("#ffffff", "#ffffff"), 1.0, abs_tol=0.01)
    assert math.isclose(C.contrast("#000000", "#000000"), 1.0, abs_tol=0.01)


def test_contrast_symmetric():
    assert C.contrast("#1e1e2e", "#cdd6f4") == C.contrast("#cdd6f4", "#1e1e2e")


def test_contrast_known_value():
    """Catppuccin Mocha text on base should be well above 10:1."""
    ratio = C.contrast("#cdd6f4", "#1e1e2e")
    assert ratio > 10.0


# ---------------------------------------------------------------------
# brighten / darken
# ---------------------------------------------------------------------

def test_brighten_increases_lightness():
    L0, _, _ = C.hex_to_oklch("#89b4fa")
    L1, _, _ = C.hex_to_oklch(C.brighten("#89b4fa", 0.1))
    assert L1 > L0


def test_darken_decreases_lightness():
    L0, _, _ = C.hex_to_oklch("#89b4fa")
    L1, _, _ = C.hex_to_oklch(C.darken("#89b4fa", 0.1))
    assert L1 < L0


def test_brighten_preserves_hue_roughly():
    _, _, H0 = C.hex_to_oklch("#89b4fa")
    _, _, H1 = C.hex_to_oklch(C.brighten("#89b4fa", 0.1))
    assert C.hue_distance(H0, H1) < 5.0


def test_brighten_clamps_at_white():
    out = C.brighten("#ffffff", 0.5)
    assert out == "#ffffff"


def test_darken_clamps_at_black():
    out = C.darken("#000000", 0.5)
    assert out == "#000000"


# ---------------------------------------------------------------------
# with_alpha
# ---------------------------------------------------------------------

def test_with_alpha_basic():
    assert C.with_alpha("#89b4fa", 1.0) == "#FF89B4FA"
    assert C.with_alpha("#89b4fa", 0.0) == "#0089B4FA"


def test_with_alpha_clamps():
    assert C.with_alpha("#89b4fa", 2.0) == "#FF89B4FA"
    assert C.with_alpha("#89b4fa", -1.0) == "#0089B4FA"


def test_with_alpha_short_form():
    assert C.with_alpha("#fff", 1.0) == "#FFFFFFFF"


# ---------------------------------------------------------------------
# ensure_readable
# ---------------------------------------------------------------------

def test_ensure_readable_passes_through_when_already_ok():
    """Black on white passes; returned unchanged."""
    assert C.ensure_readable("#000000", "#ffffff", 4.5) == "#000000"


def test_ensure_readable_fixes_low_contrast():
    """Grey text on grey background should get pushed away from bg."""
    fg = "#808080"
    bg = "#888888"
    assert C.contrast(fg, bg) < 4.5
    fixed = C.ensure_readable(fg, bg, 4.5)
    assert C.contrast(fixed, bg) >= 4.5


def test_ensure_readable_pushes_dark_fg_darker():
    """If fg is dark on light bg, it should go darker."""
    fg = "#bbbbbb"
    bg = "#ffffff"
    fixed = C.ensure_readable(fg, bg, 4.5)
    L_fg, _, _ = C.hex_to_oklch(fg)
    L_fixed, _, _ = C.hex_to_oklch(fixed)
    assert L_fixed < L_fg


def test_ensure_readable_pushes_light_fg_lighter():
    """If fg is light on dark bg, it should go lighter."""
    fg = "#444444"
    bg = "#000000"
    fixed = C.ensure_readable(fg, bg, 4.5)
    L_fg, _, _ = C.hex_to_oklch(fg)
    L_fixed, _, _ = C.hex_to_oklch(fixed)
    assert L_fixed > L_fg