"""
Orquestração do fluxo de telemetria: da fonte até a volta gravada.

Este arquivo é o centro da refatoração. O gravador antigo
(`analysis/lap_recorder.py`, 283 linhas) acumulava cinco responsabilidades:
detectar virada de volta, acumular o buffer, derivar força G, decidir se a
volta era persistível e falar direto com o SQLite — além de chamar
`lap_storage.init_db()` no próprio construtor.

Aqui sobrou o que é de fato orquestração de telemetria. A decisão de gravar
foi para `SessionManager`; a persistência está atrás de `LapRepository`, então
este serviço não sabe que existe um banco.
"""

import math
from datetime import datetime
from typing import Callable

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.interfaces.telemetry_source import TelemetrySource
from ...domain.interfaces.track_repository import TrackRepository
from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint
from ...domain.services.lap_comparator import LapComparator
from ..events.event_bus import EventBus
from ..events.events import (
    CarDetected,
    ConnectionStateChanged,
    DeltaUpdated,
    LapCompleted,
    LapDiscarded,
    LapSaveFailed,
    TelemetryReceived,
    TrackCandidatesDetected,
)
from .session_manager import SessionManager

GRAVITY = 9.81

# O id do carro chega em todo pacote. Reemitir a detecção a cada ~3s (a 60 Hz)
# cobre o caso de a UI ter sido montada depois do primeiro pacote, sem inundar
# o barramento com um evento por frame.
CAR_REEMIT_INTERVAL = 180

# Abaixo desta velocidade a derivada do vetor velocidade vira ruído numérico:
# parado ou quase parado, pequenas variações produziriam forças G absurdas.
MIN_SPEED_FOR_G_KMH = 5.0
MIN_SPEED_XZ_MS = 0.5
MIN_DT_S = 0.001

# Distância mínima para tentar adivinhar a pista pelo comprimento. Voltas muito
# curtas são saídas de box ou abandonos, e casariam com qualquer coisa.
MIN_DISTANCE_FOR_TRACK_GUESS_M = 100


class TelemetryService:
    """Consome a fonte de telemetria e publica o que acontece no barramento.

    Recebe tudo por construtor. Em particular, `lap_repository` é a interface do
    domínio — trocar SQLite por JSON não muda uma linha deste arquivo.
    """

    def __init__(
        self,
        telemetry_source: TelemetrySource,
        lap_repository: LapRepository,
        session_manager: SessionManager,
        event_bus: EventBus,
        track_catalog: TrackRepository | None = None,
        car_name_resolver: Callable[[int], str | None] | None = None,
    ):
        self._source = telemetry_source
        self._laps = lap_repository
        self._session = session_manager
        self._bus = event_bus
        self._track_catalog = track_catalog
        # Injetado como função em vez de repositório inteiro: o serviço só
        # precisa traduzir id -> "Montadora Modelo", e essa composição é do
        # catálogo CSV, não do contrato genérico de CarRepository.
        self._resolve_car_name = car_name_resolver

        self._buffer: list[TelemetryPoint] = []
        self._cumulative_distance = 0.0
        self._last_lap_count: int | None = None
        self._last_elapsed_ms: int | None = None
        self._lap_started_at: datetime | None = None

        self._prev_velocity_x: float | None = None
        self._prev_velocity_z: float | None = None
        self._prev_velocity_ms: int | None = None

        self._detected_car_id: int | None = None
        self._car_reemit_counter = 0

        # Dois comparadores independentes: contra a melhor volta da pista e
        # contra a volta imediatamente anterior. São referências diferentes e
        # ambas úteis — a melhor mostra o potencial, a anterior mostra se a
        # mudança que você acabou de fazer funcionou.
        self._comparator_best = LapComparator([])
        self._comparator_previous = LapComparator([])

        self._source.telemetry_stream.connect(self.on_frame)
        self._source.status_changed.connect(self._on_source_status)
        self._source.error_occurred.connect(self._on_source_error)

    # ---------- ciclo de vida ----------

    def start(self) -> None:
        self._reset_lap_state()
        self._load_best_reference()
        self._session.start_session()
        self._source.start()

    def stop(self) -> None:
        self._source.stop()
        self._session.end_session()
        self._reset_lap_state()

    @property
    def is_running(self) -> bool:
        return self._source.is_running

    def reload_reference(self) -> None:
        """Recarrega a melhor volta como referência do delta.

        Chamado ao trocar de pista: a referência anterior é de outra pista e
        produziria um delta sem sentido. O comparador da volta anterior também
        é zerado, pela mesma razão.
        """
        self._reset_lap_state()
        self._comparator_previous = LapComparator([])
        self._load_best_reference()

    def _load_best_reference(self) -> None:
        track_id = self._session.track_id
        if track_id is None:
            self._comparator_best = LapComparator([])
            return
        best = self._laps.get_best(track_id)
        if best is None or best.id is None:
            self._comparator_best = LapComparator([])
            return
        self._comparator_best = LapComparator(self._laps.load_points(best.id))

    def _reset_lap_state(self) -> None:
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_elapsed_ms = None
        self._lap_started_at = None
        self._prev_velocity_x = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None

    # ---------- caminho quente (~60 Hz) ----------

    def on_frame(self, frame) -> None:
        """Processa um pacote. Chamado ~60x/s — tudo aqui precisa ser barato."""

        # Virada de volta: o contador mudou, então a volta anterior fechou.
        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)

        # Pausado ou carregando: o tempo do jogo não corre, então acumular
        # amostras aqui inflaria a distância e distorceria o delta. O contador
        # de volta ainda é atualizado, para não perder a virada.
        if getattr(frame, "is_paused", False) or getattr(frame, "is_loading", False):
            self._last_lap_count = frame.lap_count
            self._last_elapsed_ms = frame.current_lap_ms
            return

        g_lateral, g_longitudinal = self._compute_g_forces(frame)

        # Distância acumulada por integração da velocidade: o GT7 não transmite
        # hodômetro por volta, e é a distância que alinha a comparação entre
        # voltas diferentes.
        if (
            self._last_elapsed_ms is not None
            and frame.current_lap_ms >= self._last_elapsed_ms
        ):
            dt_s = (frame.current_lap_ms - self._last_elapsed_ms) / 1000
            self._cumulative_distance += (frame.speed_kmh / 3.6) * dt_s

        if self._lap_started_at is None:
            self._lap_started_at = datetime.now()

        point = self._frame_to_point(frame, g_lateral, g_longitudinal)
        self._buffer.append(point)

        self._last_lap_count = frame.lap_count
        self._last_elapsed_ms = frame.current_lap_ms

        self._detect_car(frame)

        self._bus.publish(TelemetryReceived(point=point, frame=frame))
        self._publish_deltas(frame.current_lap_ms)

    def _compute_g_forces(self, frame) -> tuple[float, float]:
        """Força G lateral e longitudinal, derivando o vetor velocidade.

        O pacote traz velocidade, não aceleração. A derivada é projetada nos
        eixos do carro: o vetor velocidade normalizado dá o "para frente", e sua
        perpendicular no plano XZ dá o "para o lado". Sem a projeção, o valor
        seria aceleração no referencial do mundo — inútil para o piloto, porque
        mudaria de significado a cada curva.
        """
        if (
            self._prev_velocity_x is None
            or self._prev_velocity_ms is None
            or frame.current_lap_ms <= self._prev_velocity_ms
            or frame.speed_kmh <= MIN_SPEED_FOR_G_KMH
        ):
            self._remember_velocity(frame)
            return 0.0, 0.0

        dt = (frame.current_lap_ms - self._prev_velocity_ms) / 1000.0
        if dt <= MIN_DT_S:
            self._remember_velocity(frame)
            return 0.0, 0.0

        ax = (frame.velocity_x - self._prev_velocity_x) / dt
        az = (frame.velocity_z - self._prev_velocity_z) / dt

        speed_xz = math.sqrt(frame.velocity_x ** 2 + frame.velocity_z ** 2)
        g_lateral = g_longitudinal = 0.0
        if speed_xz > MIN_SPEED_XZ_MS:
            fwd_x = frame.velocity_x / speed_xz
            fwd_z = frame.velocity_z / speed_xz
            right_x, right_z = -fwd_z, fwd_x
            g_longitudinal = (ax * fwd_x + az * fwd_z) / GRAVITY
            g_lateral = (ax * right_x + az * right_z) / GRAVITY

        self._remember_velocity(frame)
        return g_lateral, g_longitudinal

    def _remember_velocity(self, frame) -> None:
        self._prev_velocity_x = frame.velocity_x
        self._prev_velocity_z = frame.velocity_z
        self._prev_velocity_ms = frame.current_lap_ms

    def _frame_to_point(
        self, frame, g_lateral: float, g_longitudinal: float
    ) -> TelemetryPoint:
        """DTO de fio → modelo de domínio. Única tradução entre os dois."""
        return TelemetryPoint(
            elapsed_ms=frame.current_lap_ms,
            distance_m=self._cumulative_distance,
            speed_kmh=frame.speed_kmh,
            rpm=frame.rpm,
            gear=frame.gear,
            throttle=frame.throttle,
            brake=frame.brake,
            fuel_level=frame.fuel,
            tire_temp_fl=frame.tire_temp_fl,
            tire_temp_fr=frame.tire_temp_fr,
            tire_temp_rl=frame.tire_temp_rl,
            tire_temp_rr=frame.tire_temp_rr,
            position_x=frame.position_x,
            position_z=frame.position_z,
            g_lateral=g_lateral,
            g_longitudinal=g_longitudinal,
            suspension_fl=frame.suspension_fl,
            suspension_fr=frame.suspension_fr,
            suspension_rl=frame.suspension_rl,
            suspension_rr=frame.suspension_rr,
            tire_slip_fl=frame.tire_slip_fl,
            tire_slip_fr=frame.tire_slip_fr,
            tire_slip_rl=frame.tire_slip_rl,
            tire_slip_rr=frame.tire_slip_rr,
            turbo_boost=frame.turbo_boost,
            oil_temp=frame.oil_temp,
            water_temp=frame.water_temp,
        )

    def _detect_car(self, frame) -> None:
        car_id = getattr(frame, "car_id", None)
        if not car_id or car_id <= 0 or self._resolve_car_name is None:
            return

        if car_id != self._detected_car_id:
            self._detected_car_id = car_id
            self._car_reemit_counter = 0
            name = self._resolve_car_name(car_id) or ""
            if name:
                self._bus.publish(CarDetected(car_name=name, car_id=car_id))
            return

        self._car_reemit_counter += 1
        if self._car_reemit_counter >= CAR_REEMIT_INTERVAL:
            self._car_reemit_counter = 0
            name = self._resolve_car_name(car_id) or ""
            if name:
                self._bus.publish(CarDetected(car_name=name, car_id=car_id))

    def _publish_deltas(self, current_elapsed_ms: int) -> None:
        delta_best = None
        if self._comparator_best.has_reference:
            ms = self._comparator_best.delta_ms_at(
                self._cumulative_distance, current_elapsed_ms
            )
            delta_best = None if ms is None else ms / 1000

        delta_prev = None
        if self._comparator_previous.has_reference:
            ms = self._comparator_previous.delta_ms_at(
                self._cumulative_distance, current_elapsed_ms
            )
            delta_prev = None if ms is None else ms / 1000

        self._bus.publish(
            DeltaUpdated(delta_best_s=delta_best, delta_previous_s=delta_prev)
        )

    # ---------- fechamento de volta ----------

    def _finalize_lap(self, lap_time_ms: int) -> None:
        """Fecha a volta que acabou: grava (se puder) e atualiza as referências."""
        if not self._buffer or not lap_time_ms or lap_time_ms <= 0:
            self._reset_lap_state()
            return

        points = self._buffer
        distance = self._cumulative_distance

        if distance > MIN_DISTANCE_FOR_TRACK_GUESS_M and self._track_catalog is not None:
            candidates = self._track_catalog.guess_by_length(distance)
            if candidates:
                self._bus.publish(
                    TrackCandidatesDetected(names=[t.name for t in candidates[:5]])
                )

        lap = Lap(
            track_id=self._session.track_id,
            car_id=self._session.car_id,
            lap_time_ms=lap_time_ms,
            start_time=self._lap_started_at,
            end_time=datetime.now(),
            is_player=self._session.is_player_mode,
            points=points,
        )

        if not self._session.can_persist:
            # Mesmo sem gravar, a volta vira referência para o delta "vs volta
            # anterior" — é o que mantém o delta útil enquanto o piloto ainda
            # não escolheu a pista.
            self._comparator_previous = LapComparator(points)
            self._bus.publish(
                LapDiscarded(
                    lap_time_ms=lap_time_ms,
                    reason=self._session.blocked_reason or "desconhecido",
                )
            )
            self._reset_lap_state()
            return

        try:
            previous_best = self._laps.get_best(self._session.track_id)
            lap_id = self._laps.save(lap)
            lap.id = lap_id
            is_best = previous_best is None or lap_time_ms < previous_best.lap_time_ms

            self._session.register_lap(lap)
            self._bus.publish(LapCompleted(lap=lap, lap_id=lap_id, is_best=is_best))

            self._comparator_previous = LapComparator(points)
            if is_best:
                self._comparator_best = LapComparator(points)
        except Exception as exc:  # noqa: BLE001
            # A falha vira evento visível. A versão antiga engolia isso num
            # print(): o piloto completava a volta, via tudo normal na tela e
            # só descobria a perda quando o histórico vinha vazio.
            self._bus.publish(
                LapSaveFailed(
                    message=f"Falha ao salvar volta: {exc}", lap_time_ms=lap_time_ms
                )
            )
        finally:
            self._reset_lap_state()

    # ---------- repasse de estado da fonte ----------

    def _on_source_status(self, state: str) -> None:
        self._bus.publish(ConnectionStateChanged(state=state))

    def _on_source_error(self, message: str) -> None:
        self._bus.publish(ConnectionStateChanged(state="erro", message=message))
