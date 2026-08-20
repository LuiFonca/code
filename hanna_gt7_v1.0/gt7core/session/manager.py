"""
Sessão e gravação — o que decide se uma volta vira registro.

Duas responsabilidades separadas de propósito, porque mudam por motivos
diferentes:

- `SessionManager` guarda o estado da sessão (pista, carro, modo) e responde
  **se** a volta deve ser gravada. A política vive aqui e em nenhum outro lugar.
- `RecordingService` liga o motor de telemetria ao repositório. Assina o
  fechamento de volta, consulta o gerente e persiste.

No código anterior à refatoração isso tudo morava dentro do gravador, junto com
detecção de volta e cálculo de força G — cinco responsabilidades num arquivo só.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..analytics.delta import LapComparator
from ..domain.models import Car, Lap, Session, Track
from ..events.bus import EventBus
from ..observability.logging import get_logger
from ..storage.repositories import SqliteLapRepository, SqliteSessionRepository
from ..telemetry.engine import LapBoundaryDetected

_log = get_logger(__name__)


# ---------- eventos ----------

@dataclass(frozen=True, slots=True)
class SessionStarted:
    session_id: int | None
    track_name: str | None
    car_name: str | None


@dataclass(frozen=True, slots=True)
class SessionEnded:
    session_id: int | None
    lap_count: int


@dataclass(frozen=True, slots=True)
class TrackChanged:
    track_id: int | None
    track_name: str | None


@dataclass(frozen=True, slots=True)
class CarChanged:
    car_id: int | None
    car_name: str | None


@dataclass(frozen=True, slots=True)
class LapSaved:
    lap: Lap
    lap_id: int
    is_best: bool


@dataclass(frozen=True, slots=True)
class LapDiscarded:
    lap_time_ms: int
    reason: str


@dataclass(frozen=True, slots=True)
class LapSaveFailed:
    """A gravação falhou e o piloto precisa saber.

    A versão anterior à refatoração engolia isso num `print()`: o piloto
    completava a volta, via tudo normal na tela, e só descobria a perda quando
    o histórico vinha vazio.
    """

    message: str
    lap_time_ms: int


@dataclass(frozen=True, slots=True)
class DeltaUpdated:
    delta_best_s: float | None
    delta_previous_s: float | None


# ---------- gerente de sessão ----------

class SessionManager:
    """Dono da decisão "esta volta deve ser gravada?".

    Duas condições distintas impedem a gravação, e a diferença importa para a
    mensagem que o usuário vê:

    - **Sem pista definida.** O GT7 não transmite qual pista está em uso, e sem
      isso não há onde arquivar a volta. Não existe pista-padrão: um nome
      inventado misturaria voltas de circuitos diferentes no mesmo histórico,
      corrompendo recordes e deltas.
    - **Modo replay/IA.** A telemetria continua válida e é exibida ao vivo, mas
      não é do piloto — gravá-la falsificaria os recordes.
    """

    def __init__(
        self,
        event_bus: EventBus,
        session_repository: SqliteSessionRepository | None = None,
    ) -> None:
        self._bus = event_bus
        self._sessions = session_repository
        self._session = Session()
        self._track: Track | None = None
        self._car: Car | None = None
        self._is_player_mode = True

    # ---------- estado ----------

    @property
    def session(self) -> Session:
        return self._session

    @property
    def session_id(self) -> int | None:
        return self._session.id

    @property
    def track(self) -> Track | None:
        return self._track

    @property
    def car(self) -> Car | None:
        return self._car

    @property
    def track_id(self) -> int | None:
        return self._track.id if self._track else None

    @property
    def car_id(self) -> int | None:
        return self._car.id if self._car else None

    @property
    def is_player_mode(self) -> bool:
        return self._is_player_mode

    @property
    def can_persist(self) -> bool:
        """True quando a volta que fechar agora deve ser gravada."""
        return self._track is not None and self._is_player_mode

    @property
    def blocked_reason(self) -> str | None:
        """Por que a gravação está bloqueada, em texto pronto para exibir."""
        if self._track is None:
            return "nenhuma pista definida"
        if not self._is_player_mode:
            return "modo replay/IA"
        return None

    # ---------- mudanças ----------

    def set_track(self, track: Track | None) -> bool:
        """Define a pista. Devolve True se mudou de fato.

        Pode ser chamado com a captura em andamento: trocar de pista no meio da
        sessão é um caso real e não exige reconectar.
        """
        if (track.id if track else None) == self.track_id:
            return False
        self._track = track
        self._session.track = track
        self._bus.publish(
            TrackChanged(
                track_id=track.id if track else None,
                track_name=track.name if track else None,
            )
        )
        return True

    def set_car(self, car: Car | None) -> bool:
        """Troca o carro da sessão. Devolve se algo mudou.

        A comparação inclui o **nome**, e não só o id. Desde que a identificação
        automática passou a montar o carro a partir do catálogo — em memória,
        sem tocar o banco —, o objeto chega sem id; comparando só por id, um
        carro novo (`id=None`) parecia idêntico a "nenhum carro" (`None`) e a
        troca era descartada em silêncio. O sintoma era o painel e o Discord
        mostrarem "Carro: —" a sessão inteira, com a telemetria correta.
        """
        same_id = (car.id if car else None) == self.car_id
        same_name = (car.name if car else None) == (
            self._car.name if self._car else None
        )
        if same_id and same_name:
            return False
        self._car = car
        self._session.car = car
        self._bus.publish(
            CarChanged(
                car_id=car.id if car else None, car_name=car.name if car else None
            )
        )
        return True

    def set_player_mode(self, is_player: bool) -> None:
        """Alterna entre pilotagem real e replay/IA."""
        self._is_player_mode = is_player

    # ---------- ciclo de vida ----------

    def start_session(self) -> None:
        session_id = None
        if self._sessions is not None:
            session_id = self._sessions.start(self.track_id, self.car_id)

        self._session = Session(
            id=session_id,
            car=self._car,
            track=self._track,
            start=datetime.now(),
            laps=[],
        )
        self._bus.publish(
            SessionStarted(
                session_id=session_id,
                track_name=self._track.name if self._track else None,
                car_name=self._car.name if self._car else None,
            )
        )

    def end_session(self) -> None:
        if self._session.start is None:
            return
        self._session.end = datetime.now()
        if self._sessions is not None and self._session.id is not None:
            self._sessions.finish(self._session.id, self._session.lap_count)
        self._bus.publish(
            SessionEnded(
                session_id=self._session.id, lap_count=self._session.lap_count
            )
        )

    def register_lap(self, lap: Lap) -> None:
        """Adiciona a volta à sessão — chamado depois de gravada com sucesso,
        para a sessão refletir o que de fato foi persistido."""
        self._session.add_lap(lap)


# ---------- serviço de gravação ----------

class RecordingService:
    """Liga o fechamento de volta à persistência.

    Assina `LapBoundaryDetected`, decide com o `SessionManager` e grava. Mantém
    também as duas referências de delta — contra a melhor volta da pista e
    contra a volta imediatamente anterior.

    Por que duas referências: a melhor mostra o potencial, a anterior mostra se
    a mudança que o piloto acabou de fazer funcionou.
    """

    def __init__(
        self,
        event_bus: EventBus,
        lap_repository: SqliteLapRepository,
        session_manager: SessionManager,
    ) -> None:
        self._bus = event_bus
        self._laps = lap_repository
        self._session = session_manager

        self._comparator_best = LapComparator([])
        self._comparator_previous = LapComparator([])

        self._bus.subscribe(LapBoundaryDetected, self._on_lap_boundary)

    # ---------- referências de delta ----------

    @property
    def has_best_reference(self) -> bool:
        return self._comparator_best.has_reference

    @property
    def has_previous_reference(self) -> bool:
        return self._comparator_previous.has_reference

    def reload_reference(self) -> None:
        """Recarrega a melhor volta como referência do delta.

        Chamado ao trocar de pista: a referência anterior é de outra pista e
        produziria um delta sem sentido. O comparador da volta anterior também é
        zerado, pela mesma razão.
        """
        self._comparator_previous = LapComparator([])
        track_id = self._session.track_id
        if track_id is None:
            self._comparator_best = LapComparator([])
            return

        best = self._laps.get_best(track_id)
        if best is None or best.id is None:
            self._comparator_best = LapComparator([])
            return
        self._comparator_best = LapComparator(self._laps.load_points(best.id))

    def delta_at(
        self, distance_m: float, elapsed_ms: int
    ) -> tuple[float | None, float | None]:
        """Delta em segundos contra (melhor, anterior). None onde não há
        referência ou o piloto passou do trecho coberto."""

        def to_seconds(comparator: LapComparator) -> float | None:
            if not comparator.has_reference:
                return None
            ms = comparator.delta_ms_at(distance_m, elapsed_ms)
            return None if ms is None else ms / 1000.0

        return to_seconds(self._comparator_best), to_seconds(self._comparator_previous)

    def publish_delta(self, distance_m: float, elapsed_ms: int) -> None:
        best, previous = self.delta_at(distance_m, elapsed_ms)
        self._bus.publish(DeltaUpdated(delta_best_s=best, delta_previous_s=previous))

    # ---------- gravação ----------

    def _on_lap_boundary(self, event: LapBoundaryDetected) -> None:
        lap = Lap(
            session_id=self._session.session_id,
            track_id=self._session.track_id,
            car_id=self._session.car_id,
            lap_time_ms=event.lap_time_ms,
            start_time=datetime.now(),
            end_time=datetime.now(),
            is_player=self._session.is_player_mode,
            points=event.points,
        )

        if not self._session.can_persist:
            # Mesmo sem gravar, a volta vira referência para o delta "vs volta
            # anterior" — é o que mantém o delta útil enquanto o piloto ainda
            # não escolheu a pista.
            self._comparator_previous = LapComparator(event.points)
            self._bus.publish(
                LapDiscarded(
                    lap_time_ms=event.lap_time_ms,
                    reason=self._session.blocked_reason or "desconhecido",
                )
            )
            return

        try:
            track_id = self._session.track_id
            previous_best = self._laps.get_best(track_id) if track_id else None
            lap_id = self._laps.save(lap)
            lap.id = lap_id

            is_best = (
                previous_best is None or event.lap_time_ms < previous_best.lap_time_ms
            )
            self._session.register_lap(lap)
            self._bus.publish(LapSaved(lap=lap, lap_id=lap_id, is_best=is_best))

            self._comparator_previous = LapComparator(event.points)
            if is_best:
                self._comparator_best = LapComparator(event.points)
        except Exception as error:  # noqa: BLE001
            _log.exception("falha ao gravar volta", extra={"lap_ms": event.lap_time_ms})
            self._bus.publish(
                LapSaveFailed(
                    message=f"Falha ao salvar volta: {error}",
                    lap_time_ms=event.lap_time_ms,
                )
            )
