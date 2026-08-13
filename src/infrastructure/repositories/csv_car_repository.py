"""Catálogo de carros do jogo (somente leitura)."""

from ...domain.interfaces.car_repository import CarRepository
from ...domain.models.car import Car
from .csv_catalog import CsvCatalog


class CsvCarRepository(CarRepository):
    """Os ~527 carros do GT7, vindos do CSV embarcado.

    Somente leitura: é o catálogo do jogo, não o histórico do usuário. Carros
    que o piloto usou de fato ficam no `SqliteCarRepository`.
    """

    def __init__(self, catalog: CsvCatalog):
        self._catalog = catalog

    def get_all(self) -> list[Car]:
        return sorted(self._catalog.cars.values(), key=lambda c: c.name)

    def get_by_id(self, car_id: int) -> Car | None:
        return self._catalog.cars.get(car_id)

    def find_by_name(self, name: str) -> list[Car]:
        needle = name.strip().lower()
        if not needle:
            return []
        return sorted(
            (c for c in self._catalog.cars.values() if needle in c.name.lower()),
            key=lambda c: c.name,
        )

    def get_full_name(self, car_id: int) -> str | None:
        """"Montadora Modelo" — usado pela auto-detecção, que só recebe o id
        numérico dentro do pacote de telemetria."""
        return self._catalog.car_full_name(car_id)

    def get_or_create(self, name: str) -> int:
        raise NotImplementedError(
            "O catálogo do jogo é somente leitura. Use SqliteCarRepository "
            "para registrar carros usados pelo piloto."
        )
