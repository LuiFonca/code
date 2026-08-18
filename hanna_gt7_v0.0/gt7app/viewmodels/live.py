"""
Estado da tela ao vivo — agora alimentado pelo núcleo.

Portado de `src/application/viewmodels/live_viewmodel.py`. A lógica de
apresentação é a mesma; o que mudou é de onde vêm os eventos: antes o
`EventBus` do Qt, agora o `QtEventBusAdapter`, que os traz do núcleo puro já
na thread da interface.

Os dois temporizadores continuam, com papéis distintos:

- **repaint** desacopla a taxa de renderização da taxa de chegada. A telemetria
  chega a ~60 Hz; repintar a cada pacote gasta ciclos sem ganho visível.
- **watchdog** detecta silêncio na transmissão. Existe para não confundir "carro
  parado" (velocidade 0 real) com "transmissão perdida" (ausência de dado) — a
  versão anterior à refatoração deixava o último valor congelado na tela
  indefinidamente, e os dois casos ficavam idênticos.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from gt7core.session.manager import DeltaUpdated, LapSaved
from gt7core.telemetry.engine import TelemetryReceived
from gt7core.telemetry.sources.base import ConnectionState

from ..adapters.qt_bus import QtEventBusAdapter

UI_REFRESH_HZ = 60
UI_REFRESH_INTERVAL_MS = int(1000 / UI_REFRESH_HZ)

STALE_TIMEOUT_S = 1.0
WATCHDOG_INTERVAL_MS = 250


class LiveViewModel(QObject):
    """Alimenta o painel ao vivo."""

    frame_updated = Signal(object)          # TelemetryReceived
    delta_updated = Signal(object, object)  # (delta_melhor, delta_anterior)
    connection_changed = Signal(str, str)   # (estado, mensagem)
    stale_entered = Signal()
    stale_exited = Signal()
    lap_saved = Signal(object)              # LapSaved

    def __init__(self, adapter: QtEventBusAdapter, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._adapter = adapter

        self._latest: TelemetryReceived | None = None
        self._delta_best: float | None = None
        self._delta_previous: float | None = None
        self._last_frame_at: float | None = None
        self._is_stale = False
        self._connected = False

        adapter.subscribe(TelemetryReceived, self._on_telemetry)
        adapter.subscribe(DeltaUpdated, self._on_delta)
        adapter.subscribe(LapSaved, self._on_lap_saved)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(UI_REFRESH_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._emit_latest)
        self._repaint_timer.start()

        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._check_stale)
        self._watchdog.start()

    # ---------- estado exposto ----------

    @property
    def adapter(self) -> QtEventBusAdapter:
        """A ponte de eventos, para quem precisa assinar outros tipos.

        Exposta em vez de recriada: uma segunda assinatura direta no barramento
        faria o evento atravessar a fronteira de thread duas vezes, que é
        exatamente o que o adaptador existe para evitar.
        """
        return self._adapter

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    @property
    def latest_point(self) -> object | None:
        return self._latest.point if self._latest else None

    @property
    def delta_best(self) -> float | None:
        return self._delta_best

    @property
    def delta_previous(self) -> float | None:
        return self._delta_previous

    def on_connection_state(self, state: ConnectionState, message: str = "") -> None:
        """Ligado diretamente ao `on_status` da fonte, sem passar pelo bus:
        estado de conexão é da fonte, não um fato de domínio."""
        self._connected = state in (
            ConnectionState.CONNECTING,
            ConnectionState.RECEIVING,
            ConnectionState.NO_SIGNAL,
        )
        if state == ConnectionState.DISCONNECTED:
            self._latest = None
            self._last_frame_at = None
            self._is_stale = False
        self.connection_changed.emit(str(state.value), message)

    # ---------- reação a eventos ----------

    def _on_telemetry(self, event: TelemetryReceived) -> None:
        # Só guarda; quem pinta é o timer. Fazer trabalho de interface aqui
        # colocaria a renderização no caminho quente de 60 Hz.
        self._latest = event
        self._last_frame_at = time.monotonic()
        if self._is_stale:
            self._is_stale = False
            self.stale_exited.emit()

    def _on_delta(self, event: DeltaUpdated) -> None:
        self._delta_best = event.delta_best_s
        self._delta_previous = event.delta_previous_s

    def _on_lap_saved(self, event: LapSaved) -> None:
        self.lap_saved.emit(event)

    # ---------- temporizadores ----------

    def _emit_latest(self) -> None:
        if self._is_stale or self._latest is None:
            return
        self.frame_updated.emit(self._latest)
        self.delta_updated.emit(self._delta_best, self._delta_previous)

    def _check_stale(self) -> None:
        if not self._connected or self._last_frame_at is None or self._is_stale:
            return
        if time.monotonic() - self._last_frame_at > STALE_TIMEOUT_S:
            self._is_stale = True
            self._delta_best = None
            self._delta_previous = None
            self.stale_entered.emit()

    def close(self) -> None:
        """Para os temporizadores e desinscreve. Chamado ao fechar a janela."""
        self._repaint_timer.stop()
        self._watchdog.stop()
        self._adapter.unsubscribe(TelemetryReceived, self._on_telemetry)
        self._adapter.unsubscribe(DeltaUpdated, self._on_delta)
        self._adapter.unsubscribe(LapSaved, self._on_lap_saved)
