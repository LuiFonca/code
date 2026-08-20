"""
Design system da aplicação.

Duas camadas, ambas sem Qt: `tokens` guarda os valores e `theme` os transforma
em folha de estilo. Widgets ficam em `gt7app/widgets/` porque esses sim precisam
de Qt — a separação é o que permite testar a coerência visual headless.
"""

from .theme import build_stylesheet
from .tokens import (
    DARK_THEME,
    DEFAULT_THEME,
    LIGHT_THEME,
    THEMES,
    Palette,
    Radius,
    Space,
    Theme,
    TypeScale,
    get_theme,
)

__all__ = [
    "DARK_THEME",
    "DEFAULT_THEME",
    "LIGHT_THEME",
    "THEMES",
    "Palette",
    "Radius",
    "Space",
    "Theme",
    "TypeScale",
    "build_stylesheet",
    "get_theme",
]
