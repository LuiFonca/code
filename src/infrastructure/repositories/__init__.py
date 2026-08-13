"""
Implementações concretas dos repositórios do domínio.

Duas famílias convivem de propósito:

- **SQLite** — dados do usuário (voltas gravadas, carros e pistas efetivamente
  usados). Leitura e escrita.
- **CSV** — catálogo estático do jogo (527 carros, 72 montadoras, 106 pistas).
  Somente leitura; `get_or_create` levanta `NotImplementedError`.

A UI combina os dois: o seletor de pista mostra primeiro as que já têm voltas
gravadas e, abaixo, o catálogo completo como alternativa.
"""

from .csv_car_repository import CsvCarRepository
from .csv_catalog import CsvCatalog
from .csv_maker_repository import CsvMakerRepository
from .csv_track_repository import CsvTrackRepository
from .sqlite_car_repository import SqliteCarRepository
from .sqlite_database import SqliteDatabase
from .sqlite_lap_repository import SqliteLapRepository
from .sqlite_track_repository import SqliteTrackRepository

__all__ = [
    "CsvCarRepository",
    "CsvCatalog",
    "CsvMakerRepository",
    "CsvTrackRepository",
    "SqliteCarRepository",
    "SqliteDatabase",
    "SqliteLapRepository",
    "SqliteTrackRepository",
]
