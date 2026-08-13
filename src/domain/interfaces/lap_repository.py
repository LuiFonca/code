"""Contrato de persistência de voltas."""

from abc import ABC, abstractmethod

from ..models.lap import Lap


class LapRepository(ABC):
    """Guarda e recupera voltas.

    ABC pura, sem Qt: persistência é detalhe de infraestrutura e o domínio não
    deve saber se por trás existe SQLite, JSON ou um serviço remoto.

    Convenção de carga: os métodos de **listagem** devolvem `Lap` com `points`
    vazio (barato — o histórico só precisa dos tempos). Quem precisa das
    amostras chama `get_by_id` ou `load_points`. Isso mantém a listagem do
    histórico rápida mesmo com dezenas de voltas de milhares de amostras.
    """

    @abstractmethod
    def save(self, lap: Lap) -> int:
        """Persiste a volta e devolve o id atribuído. Deve ser atômico: uma
        falha no meio não pode deixar volta sem amostras nem setores órfãos."""

    @abstractmethod
    def get_by_id(self, lap_id: int) -> Lap | None:
        """Volta completa, com `points` carregados. None se não existir."""

    @abstractmethod
    def get_all(self, limit: int | None = None) -> list[Lap]:
        """Todas as voltas, mais recentes primeiro, sem `points`."""

    @abstractmethod
    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        """Voltas de uma pista, mais recentes primeiro, sem `points`."""

    @abstractmethod
    def get_best(self, track_id: int) -> Lap | None:
        """Volta mais rápida da pista, sem `points`. None se não houver
        nenhuma volta válida registrada."""

    @abstractmethod
    def get_top(self, track_id: int, limit: int = 5) -> list[Lap]:
        """As `limit` voltas mais rápidas da pista, sem `points`."""

    @abstractmethod
    def load_points(self, lap_id: int) -> list:
        """Só as amostras de uma volta — para quando já se tem o `Lap` da
        listagem e falta apenas o detalhe."""

    @abstractmethod
    def get_sector_times(self, lap_id: int) -> list[int | None]:
        """Tempos (ms) de cada setor da volta. None num setor sem dado."""

    @abstractmethod
    def get_sector_times_batch(self, lap_ids: list[int]) -> dict[int, list[int | None]]:
        """Tempos de setor de várias voltas numa única consulta.

        Existe para evitar o padrão N+1 na tela de histórico, que antes
        consultava setor a setor dentro do laço de renderização."""

    @abstractmethod
    def delete(self, lap_id: int) -> None:
        """Remove a volta e tudo que depende dela (amostras, setores)."""

    @abstractmethod
    def delete_by_track(self, track_id: int) -> None:
        """Remove todas as voltas de uma pista, preservando a pista."""
