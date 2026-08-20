"""
Captura UDP da telemetria do GT7 — agora sem Qt.

Portado de `src/infrastructure/telemetry/listener_thread.py`. A lógica de rede
foi preservada quase inteira: ela estava correta e resolvia problemas reais
(heartbeat adaptativo, tradução de erro de rede, fechamento garantido do
socket). O que mudou é a mecânica em volta.

O original era uma `QThread` que emitia `Signal`, mais um adaptador
`Gt7TelemetrySource` que a envolvia. Aqui é uma `threading.Thread` comum e os
sinais viraram callbacks do contrato `TelemetrySource` — mesma separação, sem
arrastar Qt para dentro do núcleo.

Duas adições: contadores de pacote (§35, que não existiam) e estado tipado
(`ConnectionState` em vez das strings mágicas em português).
"""

from __future__ import annotations

import errno
import socket
import struct
import threading
import time

from ...observability.logging import get_logger
from ...observability.metrics import TelemetryMetrics
from ..protocol import TelemetryFrame, salsa20_decode
from .base import ConnectionState, TelemetrySource

_log = get_logger(__name__)

DEFAULT_SEND_PORT = 33739
DEFAULT_RECEIVE_PORT = 33740

# O PS5 para de transmitir se não receber um "toque" periódico.
HEARTBEAT_INTERVAL_S = 10.0

# Antes do primeiro pacote o toque é bem mais frequente, por dois motivos:
#
# 1. `EHOSTUNREACH` numa rede local costuma ser **transitório** — o kernel
#    responde isso enquanto a resolução ARP ainda está em curso, e uma segunda
#    tentativa logo depois normalmente passa. Com 10 s de espera a conexão
#    demorava dez segundos para subir, ou parecia falha.
# 2. Se o console foi ligado depois do app, ele só começa a transmitir a partir
#    do primeiro toque que receber.
HEARTBEAT_INTERVAL_INITIAL_S = 1.0

# Tempo sem nenhum pacote antes de reportar NO_SIGNAL. É ausência total de
# tráfego — mais grosseiro que o watchdog da interface, que vigia quadros
# válidos numa janela de ~1 s.
SOCKET_TIMEOUT_S = 3.0

RECV_BUFFER_BYTES = 4096


def describe_send_error(error: OSError, ps_ip: str) -> str:
    """Traduz a falha de envio em algo acionável.

    A mensagem crua do sistema ("[Errno 65] No route to host") não diz o que
    fazer nem para qual endereço a tentativa foi. Os números mudam entre
    sistemas — EHOSTUNREACH é 65 no macOS e 113 no Linux — então a comparação
    usa as constantes do módulo `errno`, nunca literais.
    """
    if error.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
        return (
            f"Não foi possível alcançar o PlayStation em {ps_ip} (sem rota até o "
            f"host). Confira se o IP está correto e se o console e este "
            f"computador estão na mesma rede."
        )
    if error.errno == errno.ECONNREFUSED:
        return (
            f"O PlayStation em {ps_ip} recusou a conexão. Verifique se o GT7 "
            f"está aberto numa sessão."
        )
    if error.errno in (errno.EADDRNOTAVAIL, errno.EINVAL):
        return f"Endereço inválido: {ps_ip!r}. Confira o IP digitado."
    return f"Falha ao contatar o PlayStation em {ps_ip}: {error}"


class Gt7UdpTelemetrySource(TelemetrySource):
    """Fonte real: escuta a porta UDP e emite um quadro por pacote válido.

    Satisfaz o mesmo contrato que `MockTelemetrySource` — trocar uma pela outra
    não muda nenhuma linha de quem consome.
    """

    def __init__(
        self,
        ps_ip: str,
        *,
        send_port: int = DEFAULT_SEND_PORT,
        receive_port: int = DEFAULT_RECEIVE_PORT,
        metrics: TelemetryMetrics | None = None,
    ) -> None:
        super().__init__()
        self._ps_ip = ps_ip
        self._send_port = send_port
        self._receive_port = receive_port
        self.metrics = metrics or TelemetryMetrics()

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ---------- configuração ----------

    @property
    def ps_ip(self) -> str:
        return self._ps_ip

    def set_ps_ip(self, ps_ip: str) -> None:
        """Troca o IP alvo. Só vale a partir do próximo `start()` — trocar o
        destino de uma captura em andamento misturaria dados de dois consoles."""
        self._ps_ip = ps_ip

    # ---------- ciclo de vida ----------

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return  # idempotente, conforme o contrato
        self._stop_event.clear()
        self.metrics.reset()
        self._thread = threading.Thread(
            target=self._run, name="Gt7UdpTelemetrySource", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # Espera um pouco mais que o timeout do socket: a thread pode estar
            # bloqueada em recvfrom e só perceber a parada ao expirar.
            thread.join(timeout=SOCKET_TIMEOUT_S + 0.5)
        self._thread = None
        self._emit_status(ConnectionState.DISCONNECTED)

    # ---------- laço de captura ----------

    def _run(self) -> None:
        try:
            sock = self._open_socket()
        except OSError as error:
            message = f"Não foi possível abrir a porta de captura: {error}"
            _log.error("falha ao abrir socket", extra={"port": self._receive_port})
            self._emit_status(ConnectionState.ERROR, message)
            return

        self._emit_status(ConnectionState.CONNECTING)
        last_heartbeat = 0.0
        got_first_packet = False
        last_heartbeat_error: str | None = None

        try:
            while not self._stop_event.is_set():
                now = time.time()
                interval = (
                    HEARTBEAT_INTERVAL_S
                    if got_first_packet
                    else HEARTBEAT_INTERVAL_INITIAL_S
                )
                if now - last_heartbeat > interval:
                    last_heartbeat_error = self._send_heartbeat(sock, last_heartbeat_error)
                    last_heartbeat = now

                try:
                    data, _addr = sock.recvfrom(RECV_BUFFER_BYTES)
                except TimeoutError:
                    self._emit_status(ConnectionState.NO_SIGNAL)
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise

                self.metrics.record_packet(len(data))

                decoded = salsa20_decode(data)
                if decoded is None:
                    # Pacote de outra origem na mesma porta, ou corrompido.
                    self.metrics.record_invalid()
                    continue

                if not got_first_packet:
                    got_first_packet = True
                    self._emit_status(ConnectionState.RECEIVING)
                    _log.info("primeiro pacote recebido", extra={"ps_ip": self._ps_ip})

                try:
                    frame = TelemetryFrame.from_bytes(decoded)
                except struct.error:
                    self.metrics.record_invalid()  # curto/truncado
                    continue

                self.metrics.record_frame()
                self._emit_frame(frame)
        finally:
            # `finally` garante o fechamento mesmo se algo inesperado subir —
            # senão a porta ficaria presa até o processo morrer e a reconexão
            # falharia com "endereço em uso".
            sock.close()

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self._receive_port))
        sock.settimeout(SOCKET_TIMEOUT_S)
        return sock

    def _send_heartbeat(self, sock: socket.socket, last_error: str | None) -> str | None:
        """Manda o toque. Devolve a mensagem de erro corrente (ou None)."""
        try:
            sock.sendto(b"A", (self._ps_ip, self._send_port))
        except OSError as error:
            # Só reporta quando a mensagem muda: um console desligado encheria a
            # interface com o mesmo erro repetido indefinidamente.
            message = describe_send_error(error, self._ps_ip)
            if message != last_error:
                _log.warning("heartbeat falhou", extra={"ps_ip": self._ps_ip})
                self._emit_status(ConnectionState.ERROR, message)
            return message

        if last_error is not None:
            # O toque voltou a passar depois de falhar (ARP resolvido, cabo
            # reconectado...). Avisa para a interface poder limpar o alerta —
            # sem isto um erro transitório ficaria na tela para sempre.
            self._emit_status(ConnectionState.CONNECTING)
        return None
