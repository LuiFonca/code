"""Contrato de acesso a carros."""

from abc import ABC, abstractmethod

from ..models.car import Car


class CarRepository(ABC):
    """Fonte de carros.

    Duas implementações convivem: o catálogo estático do jogo (CSV, ~527
    carros, somente leitura) e o banco do usuário (carros efetivamente usados,
    com escrita). Por isso `get_or_create` é parte do contrato — quem só lê
    pode levantar `NotImplementedError` nele.
    """

    @abstractmethod
    def get_all(self) -> list[Car]:
        """Todos os carros conhecidos, ordenados por nome."""

    @abstractmethod
    def get_by_id(self, car_id: int) -> Car | None:
        ...

    @abstractmethod
    def find_by_name(self, name: str) -> list[Car]:
        """Busca case-insensitive por trecho do nome."""

    @abstractmethod
    def get_or_create(self, name: str) -> int:
        """Id do carro com este nome, criando-o se necessário.

        Só faz sentido em repositórios com escrita; catálogos somente-leitura
        devem levantar `NotImplementedError`."""
