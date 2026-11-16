"""
Estado da tela "Ao Vivo".
"""

import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from ...domain.config import AppConfig
from ...domain.services.session_stats import FuelEstimate, fuel_estimate
from ..events.event_bus import EventBus
from ..events.events import (
    ConnectionStateChanged,
    DeltaUpdated,
    LapCompleted,
    TelemetryReceived,
)

# A telemetria chega a ~60 Hz. Repintar a cada pacote desperdiça ciclos sem
# ganho visível, então a View é atualizada num ritmo próprio a partir do último
# frame recebido.
UI_REFRESH_HZ = 60
UI_REFRESH_INTERVAL_MS = int(1000 / UI_REFRESH_HZ)

WATCHDOG_INTERVAL_MS = 250


class LiveViewModel(QObject):
    """Alimenta o dashboard ao vivo.

    Dois temporizadores, com papéis distintos:

    - **repaint** desacopla a taxa de renderização da taxa de chegada;
    - **watchdog** detecta silêncio na transmissão.

    Ambos moravam na janela principal. São regra de apresentação, não de
    montagem de janela — por isso vieram para cá, onde podem ser testados sem
    instanciar a UI inteira.
    """

    frame_updated = Signal(object)          # TelemetryPoint + frame cru
    tank_capacity_changed = Signal(float)   # só quando o valor muda
    delta_updated = Signal(object, object)  # (delta_melhor, delta_anterior)
    connection_changed = Signal(str, str)   # (estado, mensagem)
    stale_entered = Signal()
    stale_exited = Signal()
    lap_completed = Signal(object)          # LapCompleted

    def __init__(
        self,
        event_bus: EventBus,
        config: AppConfig | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._bus = event_bus
        self._config = config or AppConfig()

        self._latest: TelemetryReceived | None = None
        self._latest_delta_best: float | None = None
        self._latest_delta_previous: float | None = None
        self._last_frame_at: float | None = None
        self._is_stale = False
        self._connected = False

        self._tank_capacity: float | None = None
        # Consumo das últimas voltas. Poucas de propósito: o consumo muda com
        # o ritmo e com o mapa de motor, e uma média longa demoraria a notar.
        self._fuel_usage: deque[float] = deque(maxlen=5)

        self._bus.subscribe(TelemetryReceived, self._on_telemetry)
        self._bus.subscribe(DeltaUpdated, self._on_delta)
        self._bus.subscribe(ConnectionStateChanged, self._on_connection)
        self._bus.subscribe(LapCompleted, self._on_lap_completed)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(UI_REFRESH_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._emit_latest)
        self._repaint_timer.start()

        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._check_stale)
        self._watchdog.start()

    # ---------- estado exposto ----------

    def dispose(self) -> None:
        """Para os temporizadores e cancela as inscrições."""
        self._repaint_timer.stop()
        self._watchdog.stop()
        self._bus.unsubscribe(TelemetryReceived, self._on_telemetry)
        self._bus.unsubscribe(DeltaUpdated, self._on_delta)
        self._bus.unsubscribe(ConnectionStateChanged, self._on_connection)
        self._bus.unsubscribe(LapCompleted, self._on_lap_completed)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    @property
    def delta_best(self) -> float | None:
        return self._latest_delta_best

    @property
    def delta_previous(self) -> float | None:
        return self._latest_delta_previous

    # ---------- reação a eventos ----------

    def _on_telemetry(self, event: TelemetryReceived) -> None:
        # Só guarda; quem pinta é o timer. Fazer trabalho de UI aqui colocaria
        # a renderização no caminho quente de 60 Hz.
        self._latest = event
        self._last_frame_at = time.monotonic()
        capacity = getattr(event.frame, "fuel_capacity", None)
        if capacity and capacity > 0 and capacity != self._tank_capacity:
            self._tank_capacity = capacity
            # As outras telas mostram combustível em % do tanque e só a
            # telemetria ao vivo conhece a capacidade. Sem este aviso o
            # percentual nunca aparecia fora do painel ao vivo.
            self.tank_capacity_changed.emit(capacity)
        if self._is_stale:
            self._is_stale = False
            self.stale_exited.emit()

    def _on_delta(self, event: DeltaUpdated) -> None:
        self._latest_delta_best = event.delta_best_s
        self._latest_delta_previous = event.delta_previous_s

    def _on_connection(self, event: ConnectionStateChanged) -> None:
        self._connected = event.state in ("conectando", "recebendo", "sem_sinal")
        if event.state == "desconectado":
            self._fuel_usage.clear()
            self._latest = None
            self._last_frame_at = None
            self._is_stale = False
        self.connection_changed.emit(event.state, event.message)

    def _on_lap_completed(self, event: LapCompleted) -> None:
        lap = event.lap
        # Só volta completa: numa parcial, o consumo medido é de um pedaço.
        if lap is not None and lap.is_complete and lap.fuel_used is not None:
            self._fuel_usage.append(lap.fuel_used)
        self.lap_completed.emit(event)

    def fuel_estimate(self) -> FuelEstimate:
        """Consumo médio por volta e quantas voltas o tanque ainda dá."""
        nivel = self._latest.point.fuel_level if self._latest else None
        return fuel_estimate(list(self._fuel_usage), nivel)

    # ---------- temporizadores ----------

    def _emit_latest(self) -> None:
        if self._is_stale or self._latest is None:
            return
        self.frame_updated.emit(self._latest)
        self.delta_updated.emit(self._latest_delta_best, self._latest_delta_previous)

    def _check_stale(self) -> None:
        if not self._connected or self._last_frame_at is None or self._is_stale:
            return
        if time.monotonic() - self._last_frame_at > self._config.stale_timeout_s:
            self._is_stale = True
            self._latest_delta_best = None
            self._latest_delta_previous = None
            self.stale_entered.emit()
