"""Catálogo de montadoras do jogo (somente leitura)."""

from ...domain.interfaces.maker_repository import MakerRepository
from ...domain.models.maker import Maker
from .csv_catalog import CsvCatalog


class CsvMakerRepository(MakerRepository):
    """As ~72 montadoras do GT7."""

    def __init__(self, catalog: CsvCatalog):
        self._catalog = catalog

    def get_all(self) -> list[Maker]:
        return sorted(self._catalog.makers.values(), key=lambda m: m.name)

    def get_by_id(self, maker_id: int) -> Maker | None:
        return self._catalog.makers.get(maker_id)

    def find_by_name(self, name: str) -> list[Maker]:
        needle = name.strip().lower()
        if not needle:
            return []
        return sorted(
            (m for m in self._catalog.makers.values() if needle in m.name.lower()),
            key=lambda m: m.name,
        )
