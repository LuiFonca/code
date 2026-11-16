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


class TrackStore(TrackRepository):
    """Pistas do usuário: além de consultar, guarda o que o app aprende.

    Separada de `TrackRepository` porque o catálogo do jogo é somente leitura
    e não tem o que fazer com estes métodos. Antes eles existiam só na
    implementação SQLite e quem chamava testava com `hasattr` — o contrato
    estava na cabeça de quem escreveu, não no código.
    """

    @abstractmethod
    def list_with_lap_count(self) -> list[tuple[int, str, int]]:
        """(id, nome, nº de voltas) de cada pista já usada."""

    @abstractmethod
    def set_sector_fractions(self, track_id: int, fractions: list[float] | None) -> None:
        """Define onde terminam os setores, em frações da volta. None volta
        à divisão em partes iguais."""

    @abstractmethod
    def set_fingerprint(self, track_id: int, fingerprint: list | None) -> None:
        """Grava (ou apaga) a assinatura do traçado da pista."""

    @abstractmethod
    def get_fingerprint(self, track_id: int) -> list | None:
        ...

    @abstractmethod
    def all_fingerprints(self) -> dict[int, list]:
        """Assinaturas de todas as pistas que têm uma, por id."""
