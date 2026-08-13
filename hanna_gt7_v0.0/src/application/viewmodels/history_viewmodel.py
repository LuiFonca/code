"""
Estado da tela "Histórico".
"""

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ..events.event_bus import EventBus
from ..events.events import LapCompleted


@dataclass(slots=True)
class LapRow:
    """Uma linha da tabela, já pronta para exibir.

    A View não deve consultar repositório nem formatar tempo de setor: recebe
    isto montado. Na versão antiga a aba de histórico chamava `lap_storage`
    diretamente, o que colocava SQL dentro do widget.
    """

    lap: Lap
    car_name: str | None
    sector_times: list[int | None]
    is_best: bool


class HistoryViewModel(QObject):
    """Lista de voltas de uma pista, com busca e exclusão."""

    laps_changed = Signal(list)     # list[LapRow]
    error = Signal(str)

    def __init__(
        self,
        lap_repository: LapRepository,
        event_bus: EventBus,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._laps = lap_repository
        self._bus = event_bus
        self._track_id: int | None = None
        self._filter = ""
        self._rows: list[LapRow] = []

        # Volta nova gravada = tabela desatualizada. Recarregar por evento evita
        # que a View precise saber quando pedir refresh.
        self._bus.subscribe(LapCompleted, lambda _e: self.refresh())

    @property
    def rows(self) -> list[LapRow]:
        return self._rows

    @property
    def track_id(self) -> int | None:
        return self._track_id

    def set_track(self, track_id: int | None) -> None:
        self._track_id = track_id
        self.refresh()

    def set_filter(self, text: str) -> None:
        """Filtra por nome do carro, id ou tempo. Aplicado sobre o que já está
        em memória — não vai ao banco a cada tecla digitada."""
        self._filter = (text or "").strip().lower()
        self.laps_changed.emit(self._apply_filter(self._rows))

    def refresh(self) -> None:
        if self._track_id is None:
            self._rows = []
            self.laps_changed.emit([])
            return

        laps = self._laps.get_by_track(self._track_id)
        if not laps:
            self._rows = []
            self.laps_changed.emit([])
            return

        lap_ids = [lap.id for lap in laps if lap.id is not None]

        # Duas consultas em lote no lugar de duas por volta. Com 50 voltas, a
        # versão antiga fazia 101 consultas para desenhar a tabela.
        sectors = self._laps.get_sector_times_batch(lap_ids)
        car_names = (
            self._laps.car_names_batch(lap_ids)
            if hasattr(self._laps, "car_names_batch")
            else {}
        )

        best_time = min(
            (lap.lap_time_ms for lap in laps if lap.lap_time_ms > 0), default=None
        )

        self._rows = [
            LapRow(
                lap=lap,
                car_name=car_names.get(lap.id),
                sector_times=sectors.get(lap.id, []),
                is_best=(lap.lap_time_ms == best_time and best_time is not None),
            )
            for lap in laps
        ]
        self.laps_changed.emit(self._apply_filter(self._rows))

    def delete_lap(self, lap_id: int) -> None:
        try:
            self._laps.delete(lap_id)
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível excluir a volta: {exc}")

    def clear_track(self) -> None:
        """Apaga todas as voltas da pista. A confirmação é da View — o
        ViewModel assume que a decisão já foi tomada."""
        if self._track_id is None:
            return
        try:
            self._laps.delete_by_track(self._track_id)
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível limpar os dados: {exc}")

    def _apply_filter(self, rows: list[LapRow]) -> list[LapRow]:
        if not self._filter:
            return rows
        needle = self._filter
        return [
            r
            for r in rows
            if needle in (r.car_name or "").lower()
            or needle in str(r.lap.id or "")
            or needle in self._format_time(r.lap.lap_time_ms)
        ]

    @staticmethod
    def _format_time(ms: int) -> str:
        if not ms or ms < 0:
            return ""
        total = ms / 1000
        return f"{int(total // 60)}:{total % 60:06.3f}"
