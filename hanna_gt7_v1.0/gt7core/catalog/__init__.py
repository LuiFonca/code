"""
Catálogo estático do GT7 — o que o protocolo não informa.

O pacote de telemetria traz um `car_id` numérico e silêncio sobre a pista. Sem
esta tabela, "carro 24 em algum lugar" é tudo o que se sabe.
"""

from .catalog import (
    DEFAULT_DATA_DIR,
    MIN_DISTANCE_FOR_GUESS_M,
    CatalogCar,
    CatalogTrack,
    GameCatalog,
)

__all__ = [
    "CatalogCar",
    "CatalogTrack",
    "DEFAULT_DATA_DIR",
    "GameCatalog",
    "MIN_DISTANCE_FOR_GUESS_M",
]
