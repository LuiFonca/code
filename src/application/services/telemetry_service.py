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

from PySide6.QtCore import QTimer

from ...domain.config import AppConfig
from ...domain.interfaces.lap_repository import LapRepository
from ...domain.interfaces.telemetry_source import TelemetrySource
from ...domain.interfaces.track_repository import TrackRepository
from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint
from ...domain.services.lap_comparator import LapComparator
from ...domain.services.slip_angle import slip_angle_deg
from ..events.event_bus import EventBus
from ..events.events import (
    CarDetected,
    ConnectionStateChanged,
    DeltaUpdated,
    LapCompleted,
    LapDeleted,
    LapDiscarded,
    LapSaveFailed,
    LapsPurged,
    TelemetryReceived,
    TrackCandidatesDetected,
)
from .lap_writer import LapWriter
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

# Distância mínima para tentar adivinhar a pista pelo comprimento. Voltas muito
# curtas são saídas de box ou abandonos, e casariam com qualquer coisa.
MIN_DISTANCE_FOR_TRACK_GUESS_M = 100

# Teto de sanidade para as forças G derivadas. Carro de corrida real não passa
# de ~2 g; 5 g dá folga para qualquer caso legítimo e ainda barra o lixo. Sem
# isto, um pacote perdido ou um reset de posição no jogo produz um valor
# absurdo que é gravado no banco e estica a escala do gráfico, achatando a
# volta inteira numa linha reta.
MAX_G = 5.0

# Intervalo aceitável entre amostras para derivar aceleração. A telemetria vem
# a ~60 Hz (16,7 ms); fora desta faixa houve perda de pacote ou salto de tempo,
# e a derivada não significa nada.
MIN_DT_S = 0.001
MAX_DT_S = 0.25


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
        config: AppConfig | None = None,
    ):
        self._config = config or AppConfig()
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

        # A volta em curso foi observada desde o começo? Falso logo após
        # conectar, porque o piloto já estava na pista quando o app abriu.
        # Só vira verdadeiro depois de presenciar uma virada de volta — daí em
        # diante o buffer cobre a volta inteira.
        self._lap_observed_from_start = False

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
        # Melhor tempo conhecido da pista, mantido em memória para não
        # depender de leitura do banco no fechamento da volta (ver _finalize_lap).
        self._best_lap_time_ms: int | None = None

        # Gravação fora da thread da interface: ao cruzar a linha de chegada,
        # escrever milhares de amostras no SQLite segurava a tela por dezenas
        # de milissegundos — justamente quando o delta importa.
        self._writer = LapWriter(
            lap_repository=lap_repository,
            on_saved=self._on_lap_written,
            on_error=self._on_lap_write_failed,
        )

        # Reconexão automática. O temporizador é de disparo único e recriado a
        # cada tentativa: um QTimer repetitivo continuaria disparando depois de
        # a conexão voltar, e desligá-lo no momento certo seria mais uma coisa
        # para errar.
        self._reconnect_timer = QTimer()
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)
        self._reconnect_attempt = 0
        # Distingue queda de desconexão pedida — só a primeira religa.
        self._stop_requested = False

        self._source.telemetry_stream.connect(self.on_frame)
        self._source.status_changed.connect(self._on_source_status)
        self._source.error_occurred.connect(self._on_source_error)

        # Sem isto, excluir a melhor volta no histórico deixava o delta
        # comparando contra uma volta que não existe mais.
        self._bus.subscribe(LapDeleted, self._on_lap_deleted)

    def _on_lap_deleted(self, event: LapDeleted) -> None:
        """Recarrega a referência quando a volta apagada era a melhor."""
        if event.track_id is not None and event.track_id != self._session.track_id:
            return
        self._load_best_reference()

    # ---------- ciclo de vida ----------

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnect_timer.isActive()

    def cancel_reconnect(self) -> None:
        """Desiste de reconectar sem fechar o app."""
        self._reconnect_timer.stop()
        self._reset_reconnect_backoff()
        self._bus.publish(
            ConnectionStateChanged(state="desconectado", message="Reconexão cancelada.")
        )

    def _reset_reconnect_backoff(self) -> None:
        self._reconnect_attempt = 0

    def _next_reconnect_delay(self) -> float:
        """Recuo exponencial com teto.

        Console desligado pode ficar assim por horas; tentar a cada segundo
        inundaria a rede e o log sem chance de sucesso.
        """
        atraso = self._config.reconnect_initial_delay_s * (2 ** self._reconnect_attempt)
        self._reconnect_attempt += 1
        return min(atraso, self._config.reconnect_max_delay_s)

    def _schedule_reconnect(self) -> None:
        if not self._config.auto_reconnect or self._stop_requested:
            return
        if self._reconnect_timer.isActive():
            return
        atraso = self._next_reconnect_delay()
        self._bus.publish(
            ConnectionStateChanged(
                state="reconectando",
                message=(
                    f"Sinal perdido. Tentativa {self._reconnect_attempt} de "
                    f"reconexão em {atraso:.0f}s."
                ),
            )
        )
        self._reconnect_timer.start(int(atraso * 1000))

    def _attempt_reconnect(self) -> None:
        if self._stop_requested or self._source.is_running:
            return
        self._source.start()
        if self._source.is_running:
            # Só zera o recuo quando a fonte de fato subiu; zerar na tentativa
            # faria o intervalo nunca crescer com o console desligado.
            self._reset_reconnect_backoff()
        else:
            self._schedule_reconnect()

    def start(self) -> None:
        self._stop_requested = False
        self._reconnect_timer.stop()
        self._reset_reconnect_backoff()
        # observed_from_start=False: o piloto já podia estar na pista quando o
        # app conectou, então a volta em curso não conta como completa.
        self._reset_lap_state(observed_from_start=False)
        self._load_best_reference()
        self._session.start_session()
        self._writer.start()
        self._source.start()

    def stop(self) -> None:
        # Marcado antes de parar a fonte: o status "desconectado" que ela emite
        # não pode ser confundido com uma queda e disparar reconexão.
        self._stop_requested = True
        self._reconnect_timer.stop()
        self._reset_reconnect_backoff()
        self._source.stop()
        self._session.end_session()
        # Escoa a fila antes de encerrar: fechar logo após cruzar a linha não
        # pode descartar a volta que acabou de ser feita.
        self._writer.stop()
        self._reset_lap_state(observed_from_start=False)

    @property
    def is_running(self) -> bool:
        return self._source.is_running

    def reload_reference(self) -> None:
        """Recarrega a melhor volta como referência do delta.

        Chamado ao trocar de pista: a referência anterior é de outra pista e
        produziria um delta sem sentido. O comparador da volta anterior também
        é zerado, pela mesma razão.
        """
        self._reset_lap_state(observed_from_start=False)
        self._comparator_previous = LapComparator([])
        self._load_best_reference()

    def _load_best_reference(self) -> None:
        """Recarrega a referência do delta a partir do banco.

        É também onde o melhor tempo em memória é semeado — chamado ao iniciar,
        ao trocar de pista e quando uma volta sai da disputa (exclusão ou
        marcação de inválida).
        """
        track_id = self._session.track_id
        if track_id is None:
            self._comparator_best = LapComparator([])
            self._best_lap_time_ms = None
            return
        best = self._laps.get_best(track_id)
        if best is None or best.id is None:
            self._comparator_best = LapComparator([])
            self._best_lap_time_ms = None
            return
        self._best_lap_time_ms = best.lap_time_ms
        self._comparator_best = LapComparator(self._laps.load_points(best.id))

    def _reset_lap_state(self, observed_from_start: bool = False) -> None:
        """Zera o acúmulo da volta.

        `observed_from_start` é o que distingue os dois motivos de zerar: uma
        virada de volta presenciada (a próxima volta começa do zero de verdade)
        de uma conexão nova ou troca de pista (a volta em curso já estava
        rolando e o app só vê o pedaço final).
        """
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_elapsed_ms = None
        self._lap_started_at = None
        self._prev_velocity_x = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None
        self._lap_observed_from_start = observed_from_start

    # ---------- caminho quente (~60 Hz) ----------

    def on_frame(self, frame) -> None:
        """Processa um pacote. Chamado ~60x/s — tudo aqui precisa ser barato."""

        # Virada de volta: o contador mudou, então a volta anterior fechou.
        # A partir daqui a próxima volta é observada desde o início.
        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)
            self._lap_observed_from_start = True

        # Pausado, carregando ou fora da pista: o tempo do jogo não corre (ou o
        # carro não está em volta), então **acumular** aqui inflaria a distância
        # e distorceria o delta.
        #
        # O que se suspende é só o acúmulo no buffer — a amostra continua sendo
        # publicada e o painel ao vivo segue mostrando velocidade, marcha e
        # pedais. Bloquear também a exibição apagaria a tela inteira sempre que
        # uma dessas flags viesse marcada, e as flags do GT7 são justamente a
        # parte do protocolo que veio de engenharia reversa: uma leitura errada
        # não pode custar o dashboard.
        suspend_accumulation = (
            getattr(frame, "is_paused", False)
            or getattr(frame, "is_loading", False)
            or not getattr(frame, "is_on_track", True)
        )

        g_lateral, g_longitudinal = self._compute_g_forces(frame)

        # Distância acumulada por integração da velocidade: o GT7 não transmite
        # hodômetro por volta, e é a distância que alinha a comparação entre
        # voltas diferentes.
        if (
            not suspend_accumulation
            and self._last_elapsed_ms is not None
            and frame.current_lap_ms >= self._last_elapsed_ms
        ):
            dt_s = (frame.current_lap_ms - self._last_elapsed_ms) / 1000
            self._cumulative_distance += (frame.speed_kmh / 3.6) * dt_s

        point = self._frame_to_point(frame, g_lateral, g_longitudinal)

        if not suspend_accumulation:
            if self._lap_started_at is None:
                self._lap_started_at = datetime.now()
            self._buffer.append(point)

        self._last_lap_count = frame.lap_count
        self._last_elapsed_ms = frame.current_lap_ms

        self._detect_car(frame)

        # Publicado sempre: o painel ao vivo não depende de a volta estar
        # sendo gravada.
        self._bus.publish(TelemetryReceived(point=point, frame=frame))
        if not suspend_accumulation:
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
        if dt <= MIN_DT_S or dt > MAX_DT_S:
            # Intervalo fora da faixa de 60 Hz: houve perda de pacote ou salto
            # de tempo. Derivar aqui produziria uma aceleração fictícia.
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
        return self._clamp_g(g_lateral), self._clamp_g(g_longitudinal)

    def _clamp_g(self, value: float) -> float:
        """Satura a força G no teto de sanidade.

        Saturar em vez de descartar a amostra preserva o alinhamento por
        distância: um buraco na série desalinharia a comparação entre voltas,
        que é justamente o que o eixo de distância existe para garantir.
        """
        teto = self._config.max_g
        return max(-teto, min(teto, value))

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
            slip_angle_deg=slip_angle_deg(
                frame.velocity_x,
                frame.velocity_z,
                getattr(frame, "rotation_i", 0.0),
                getattr(frame, "rotation_j", 0.0),
                getattr(frame, "rotation_k", 0.0),
                getattr(frame, "rotation_w", 0.0),
            ),
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

        is_complete = self._lap_observed_from_start
        lap = Lap(
            track_id=self._session.track_id,
            car_id=self._session.car_id,
            lap_time_ms=lap_time_ms,
            start_time=self._lap_started_at,
            end_time=datetime.now(),
            is_player=self._session.is_player_mode,
            is_complete=is_complete,
            points=points,
        )

        if not self._session.can_persist:
            # Mesmo sem gravar, a volta vira referência para o delta "vs volta
            # anterior" — é o que mantém o delta útil enquanto o piloto ainda
            # não escolheu a pista. Só se for completa: metade de volta como
            # referência faria o delta morrer no meio da pista.
            if is_complete:
                self._comparator_previous = LapComparator(points)
            self._bus.publish(
                LapDiscarded(
                    lap_time_ms=lap_time_ms,
                    reason=self._session.blocked_reason or "desconhecido",
                )
            )
            self._reset_lap_state(observed_from_start=True)
            return

        try:
            # O melhor tempo é mantido em memória, não relido do banco.
            #
            # Consultar o banco aqui seria uma corrida: a gravação é assíncrona,
            # e duas voltas em sequência rápida fariam a segunda ler um estado
            # em que a primeira ainda não entrou — e se declarar recorde
            # indevidamente. Em pista real as voltas ficam minutos apart e o
            # problema não apareceria, o que o torna pior: seria um bug raro,
            # dependente de tempo, impossível de reproduzir sob demanda.
            is_best = is_complete and (
                self._best_lap_time_ms is None
                or lap_time_ms < self._best_lap_time_ms
            )
            if is_best:
                self._best_lap_time_ms = lap_time_ms

            # Os comparadores só dependem das amostras que já estão em memória,
            # então o delta da próxima volta fica correto imediatamente, sem
            # esperar o banco.
            if is_complete:
                self._comparator_previous = LapComparator(points)
                if is_best:
                    self._comparator_best = LapComparator(points)

            self._session.register_lap(lap)
            self._writer.submit(lap, context=is_best)
        except Exception as exc:  # noqa: BLE001
            self._bus.publish(
                LapSaveFailed(
                    message=f"Falha ao preparar gravação: {exc}",
                    lap_time_ms=lap_time_ms,
                )
            )
        finally:
            self._reset_lap_state(observed_from_start=True)

    # ---------- retorno da gravação (thread do gravador) ----------

    def _on_lap_written(self, lap: Lap, lap_id: int, purged: int, is_best) -> None:
        """Chamado na thread de gravação. Só publica eventos — o barramento faz
        a troca de thread, e a interface recebe já na thread dela."""
        lap.id = lap_id
        if purged:
            self._bus.publish(LapsPurged(count=purged, track_id=lap.track_id))
        self._bus.publish(
            LapCompleted(lap=lap, lap_id=lap_id, is_best=bool(is_best))
        )

    def _on_lap_write_failed(self, lap: Lap, exc: Exception) -> None:
        """A falha vira evento visível. A versão antiga engolia isso num
        print(): o piloto completava a volta, via tudo normal na tela e só
        descobria a perda quando o histórico vinha vazio."""
        self._bus.publish(
            LapSaveFailed(
                message=f"Falha ao salvar volta: {exc}", lap_time_ms=lap.lap_time_ms
            )
        )

    # ---------- repasse de estado da fonte ----------

    def _on_source_status(self, state: str) -> None:
        if state == "recebendo":
            # Telemetria voltando é a única prova de que a conexão está boa.
            self._reset_reconnect_backoff()
        elif state == "sem_sinal" and not self._source.is_running:
            # A fonte morreu sem ninguém pedir: candidato a reconexão.
            self._schedule_reconnect()
            return
        self._bus.publish(ConnectionStateChanged(state=state))

    def _on_source_error(self, message: str) -> None:
        self._bus.publish(ConnectionStateChanged(state="erro", message=message))
