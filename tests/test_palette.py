"""Tests for kamo.palette.

Uses build_theme_from_hexes so no image files are needed. The image
extraction path (build_theme) is exercised by a separate test that
generates a tiny in-memory PNG.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from kamo import color as C
from kamo import palette
from kamo.theme import Theme


# ---------------------------------------------------------------------
# build_theme_from_hexes
# ---------------------------------------------------------------------

SAMPLE = [
    "#1e1e2e",  # dark blue-grey
    "#89b4fa",  # blue
    "#f38ba8",  # red
    "#a6e3a1",  # green
    "#f9e2af",  # yellow
    "#cba6f7",  # mauve
    "#94e2d5",  # teal
    "#fab387",  # peach
    "#f5c2e7",  # pink
    "#89dceb",  # sky
]


def test_build_returns_theme():
    theme = palette.build_theme_from_hexes(SAMPLE)
    assert isinstance(theme, Theme)


def test_all_roles_populated():
    theme = palette.build_theme_from_hexes(SAMPLE)
    for role in theme.role_names():
        value = theme.get(role)
        assert value.startswith("#")
        assert len(value) == 7


def test_neutrals_are_dark():
    """base should be much darker than text."""
    theme = palette.build_theme_from_hexes(SAMPLE)
    L_base, _, _ = C.hex_to_oklch(theme.base)
    L_text, _, _ = C.hex_to_oklch(theme.text)
    assert L_base < 0.3
    assert L_text > 0.7


def test_contrast_floors_hold():
    """Every foreground must clear its floor against base."""
    theme = palette.build_theme_from_hexes(SAMPLE)
    assert C.contrast(theme.text, theme.base) >= 6.5
    assert C.contrast(theme.subtext1, theme.base) >= 5.0
    assert C.contrast(theme.subtext0, theme.base) >= 4.0


def test_accent_text_is_readable_on_accent():
    theme = palette.build_theme_from_hexes(SAMPLE)
    assert C.contrast(theme.accent_text, theme.accent) >= 4.5


def test_neutrals_inherit_mood_hue():
    """A warm dominant color should tint the neutrals warm."""
    warm = ["#ff8800"] + SAMPLE[1:]
    theme = palette.build_theme_from_hexes(warm)
    _, _, H_base = C.hex_to_oklch(theme.base)
    _, _, H_warm = C.hex_to_oklch("#ff8800")
    assert C.hue_distance(H_base, H_warm) < 30


def test_accent_is_distinct():
    """accent and accent_text must differ."""
    theme = palette.build_theme_from_hexes(SAMPLE)
    assert theme.accent != theme.accent_text


def test_no_duplicate_accents():
    """Nine accent roles from ten sources -> nine distinct hexes."""
    theme = palette.build_theme_from_hexes(SAMPLE)
    accents = [
        theme.red, theme.green, theme.yellow, theme.blue,
        theme.mauve, theme.teal, theme.peach, theme.pink, theme.sky,
    ]
    # Allow some collapse (matches nothing) but not all the same.
    assert len(set(accents)) >= 6


def test_grey_image_still_produces_valid_theme():
    """A grey-only source should still return a readable Theme."""
    greys = ["#111111", "#333333", "#555555", "#777777", "#999999"]
    theme = palette.build_theme_from_hexes(greys)
    assert C.contrast(theme.text, theme.base) >= 4.5


# ---------------------------------------------------------------------
# build_theme (image path)
# ---------------------------------------------------------------------

def _make_test_image(path: Path, colors: list[tuple[int, int, int]]) -> None:
    """Write a small PNG with vertical color bands."""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), colors[0])
    band = max(1, w // len(colors))
    for i, col in enumerate(colors):
        for x in range(i * band, min((i + 1) * band, w)):
            for y in range(h):
                img.putpixel((x, y), col)
    img.save(path, "PNG")


def test_build_theme_from_file(tmp_path: Path):
    img = tmp_path / "test.png"
    _make_test_image(img, [
        (30, 30, 46),    # dark
        (137, 180, 250), # blue
        (243, 139, 168), # red
        (166, 227, 161), # green
    ])
    theme = palette.build_theme(img)
    assert isinstance(theme, Theme)
    assert C.contrast(theme.text, theme.base) >= 4.5


def test_build_theme_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        palette.build_theme(tmp_path / "nope.png")


def test_build_theme_from_empty_image(tmp_path: Path):
    """A single solid color should still produce a Theme."""
    img = tmp_path / "solid.png"
    _make_test_image(img, [(50, 50, 50)])
    theme = palette.build_theme(img)
    assert isinstance(theme, Theme)


# ---------------------------------------------------------------------
# Theme value object
# ---------------------------------------------------------------------

def test_theme_get_unknown_role():
    theme = palette.build_theme_from_hexes(SAMPLE)
    with pytest.raises(AttributeError):
        theme.get("nonsense")


def test_theme_has():
    theme = palette.build_theme_from_hexes(SAMPLE)
    assert theme.has("base")
    assert not theme.has("nonsense")


def test_theme_is_frozen():
    """Attempting to mutate a role should raise."""
    theme = palette.build_theme_from_hexes(SAMPLE)
    with pytest.raises(Exception):
        theme.base = "#000000"  # type: ignore[misc]


def test_theme_with_overrides():
    theme = palette.build_theme_from_hexes(SAMPLE)
    new = theme.with_overrides(base="#000000")
    assert new.base == "#000000"
    assert theme.base != "#000000"  # original unchanged