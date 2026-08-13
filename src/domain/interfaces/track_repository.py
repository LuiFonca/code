"""Contrato de acesso a pistas."""

from abc import ABC, abstractmethod

from ..models.track import Track


class TrackRepository(ABC):
    """Fonte de pistas.

    Como em `CarRepository`, convivem o catálogo estático (CSV, ~106 pistas) e
    o banco do usuário (pistas já utilizadas, com escrita).
    """

    @abstractmethod
    def get_all(self) -> list[Track]:
        """Todas as pistas conhecidas, ordenadas por nome."""

    @abstractmethod
    def get_by_id(self, track_id: int) -> Track | None:
        ...

    @abstractmethod
    def find_by_name(self, name: str) -> list[Track]:
        """Busca case-insensitive por trecho do nome."""

    @abstractmethod
    def guess_by_length(
        self, lap_distance_m: float, tolerance_pct: float = 5.0
    ) -> list[Track]:
        """Pistas cujo comprimento bate com a distância medida na volta.

        Base da auto-identificação: o GT7 não transmite qual pista está em uso,
        então o comprimento da volta é a única pista disponível. Devolve os
        candidatos ordenados do mais provável para o menos — pode vir vazio, e
        frequentemente vem mais de um (traçados de comprimento parecido)."""

    @abstractmethod
    def get_or_create(self, name: str) -> int:
        """Id da pista com este nome, criando-a se necessário. Catálogos
        somente-leitura devem levantar `NotImplementedError`."""
