"""
Contrato de fonte de telemetria — sem Qt.

A versão anterior (`src/domain/interfaces/telemetry_source.py`) era um `QObject`
com três `Signal`. Funcionava, mas era a **única** dependência de Qt no domínio,
e por isso o núcleo inteiro precisava de um event loop gráfico para existir.

Aqui os sinais viram callbacks registrados. O ganho não é estético: com este
contrato, `MockTelemetrySource` e `ReplayTelemetrySource` satisfazem a mesma
interface que a fonte UDP real, e a aplicação **não sabe** qual está rodando.
É o que atende ao replay pedido no briefing (§40) e ao suporte a outros
simuladores (§42) sem nenhum código a mais.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum

from ..protocol import TelemetryFrame

_log = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    """Estado da fonte de telemetria.

    Substitui as strings mágicas em português que a auditoria registrou como
    P12 (`"conectando"`, `"recebendo"`, `"sem_sinal"`, `"desconectado"`),
    comparadas como literais em quatro arquivos de camadas diferentes. Um erro
    de digitação ali falhava em silêncio; aqui falha no import.

    `StrEnum` (3.11+) mantém serializável em log, JSON e banco sem conversão.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    RECEIVING = "receiving"
    NO_SIGNAL = "no_signal"
    ERROR = "error"


FrameCallback = Callable[[TelemetryFrame], None]
StatusCallback = Callable[[ConnectionState, str], None]


class TelemetrySource(ABC):
    """Fonte de quadros de telemetria.

    Implementações: `Gt7UdpTelemetrySource` (real), `MockTelemetrySource`
    (sintética) e `ReplayTelemetrySource` (arquivo gravado).

    Contrato:
    - `start()` é idempotente — chamar duas vezes não abre duas capturas;
    - `stop()` é idempotente e seguro mesmo sem `start()` anterior;
    - callbacks são chamados na thread da fonte, não na de quem registrou.
      Quem precisa de outra thread instala um adaptador (ver `EventBus`).
    """

    def __init__(self) -> None:
        self._frame_callbacks: list[FrameCallback] = []
        self._status_callbacks: list[StatusCallback] = []
        self._callback_lock = threading.RLock()

    # ---------- registro ----------

    def on_frame(self, callback: FrameCallback) -> None:
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def on_status(self, callback: StatusCallback) -> None:
        with self._callback_lock:
            if callback not in self._status_callbacks:
                self._status_callbacks.append(callback)

    def adopt_callbacks_from(self, other: TelemetrySource) -> None:
        """Assume as inscrições de outra fonte.

        Existe para trocar a fonte com o programa aberto — sair do gerador
        sintético para o PS5 sem reiniciar. Quem se inscreveu (o motor, a
        interface) inscreveu-se no *objeto* fonte, não num registro central, e
        sem isto a fonte nova nasceria muda: a captura funcionaria, os quadros
        seriam produzidos, e nada na tela se mexeria.

        Mora aqui, e não em quem faz a troca, porque as listas são privadas
        desta classe. Um método público custa três linhas e evita que a camada
        de cima aprenda a alcançar `_frame_callbacks`.
        """
        if other is self:
            return
        with other._callback_lock:  # noqa: SLF001  (mesma classe)
            frames = list(other._frame_callbacks)  # noqa: SLF001
            status = list(other._status_callbacks)  # noqa: SLF001
        for frame_callback in frames:
            self.on_frame(frame_callback)
        for status_callback in status:
            self.on_status(status_callback)

    # ---------- emissão (para as subclasses) ----------

    def _emit_frame(self, frame: TelemetryFrame) -> None:
        with self._callback_lock:
            callbacks = list(self._frame_callbacks)
        for callback in callbacks:
            try:
                callback(frame)
            except Exception:
                # Mesma regra do EventBus: um consumidor quebrado não derruba a
                # captura. Sem isto, um bug na UI mataria a gravação da volta.
                _log.exception("callback de frame falhou")

    def _emit_status(self, state: ConnectionState, message: str = "") -> None:
        with self._callback_lock:
            callbacks = list(self._status_callbacks)
        for callback in callbacks:
            try:
                callback(state, message)
            except Exception:
                _log.exception("callback de status falhou")

    # ---------- ciclo de vida ----------

    @abstractmethod
    def start(self) -> None:
        """Começa a produzir quadros. Idempotente."""

    @abstractmethod
    def stop(self) -> None:
        """Para de produzir quadros. Idempotente."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True enquanto a fonte está produzindo."""
