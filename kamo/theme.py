"""The canonical role set every adapter speaks.

A Theme is an immutable snapshot of one wallpaper's palette, resolved
into named roles. Adapters read roles from it; nothing outside the
palette builder writes to it.

Adding a role is a breaking change: every adapter that hardcodes role
names must be updated. If a new role is only used by one app, keep it
in that adapter instead of here.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, fields, replace


@dataclass(frozen=True)
class Theme:
    # --- backgrounds ---------------------------------------------------
    base: str          # app background, terminal background
    mantle: str        # recessed background, statusline
    crust: str         # deepest background, rarely used

    # --- mid tones -----------------------------------------------------
    surface0: str      # cards, secondary surfaces
    surface1: str      # hover backgrounds, active surfaces
    surface2: str      # borders, separators

    # --- dim foregrounds -----------------------------------------------
    overlay0: str      # comments, disabled text
    overlay1: str      # subtle UI text
    overlay2: str      # mid-contrast text

    # --- foregrounds ---------------------------------------------------
    text: str          # main text
    subtext0: str      # secondary text
    subtext1: str      # slightly brighter secondary text

    # --- primary highlight --------------------------------------------
    accent: str        # primary brand / highlight color
    accent_text: str   # text that sits on top of `accent`

    # --- semantic ------------------------------------------------------
    red: str
    green: str
    yellow: str
    blue: str

    # --- extras --------------------------------------------------------
    mauve: str
    teal: str
    peach: str
    pink: str
    sky: str

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, role: str) -> str:
        """Look up a role by name. Raises AttributeError if unknown."""
        try:
            return getattr(self, role)
        except AttributeError as e:
            raise AttributeError(f"Theme has no role {role!r}") from e

    def has(self, role: str) -> bool:
        return role in self.role_names()

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def with_overrides(self, **kwargs: str) -> "Theme":
        """Return a new Theme with the given roles replaced.

        Used by adapters that need a one-off variant (e.g. a lightened
        border) without mutating the shared Theme.
        """
        for k in kwargs:
            if k not in self.role_names():
                raise AttributeError(f"Theme has no role {k!r}")
        return replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Reflection
    # ------------------------------------------------------------------

    @classmethod
    def role_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def background_roles(cls) -> list[str]:
        return ["base", "mantle", "crust"]

    @classmethod
    def foreground_roles(cls) -> list[str]:
        return ["text", "subtext0", "subtext1"]

    @classmethod
    def accent_roles(cls) -> list[str]:
        return [
            "accent",
            "red", "green", "yellow", "blue",
            "mauve", "teal", "peach", "pink", "sky",
        ]