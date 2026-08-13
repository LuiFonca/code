"""Contrato de acesso a montadoras."""

from abc import ABC, abstractmethod

from ..models.maker import Maker


class MakerRepository(ABC):
    """Fonte de montadoras. Somente leitura na prática — o catálogo de
    fabricantes do GT7 é estático."""

    @abstractmethod
    def get_all(self) -> list[Maker]:
        ...

    @abstractmethod
    def get_by_id(self, maker_id: int) -> Maker | None:
        ...

    @abstractmethod
    def find_by_name(self, name: str) -> list[Maker]:
        """Busca case-insensitive por trecho do nome."""
