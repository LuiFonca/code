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

import contextlib
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

# Quanto o `recvfrom` espera antes de devolver o controle ao laço.
#
# Era o próprio SOCKET_TIMEOUT_S, e isso amarrava duas coisas que não têm
# relação: de quanto em quanto tempo a thread **acorda** e depois de quanto
# silêncio se declara NO_SIGNAL. Com 3 s de espera, parar a captura levava
# até 3 s — a thread só via o pedido de parada ao expirar —, e fechar o
# socket de fora não resolve: no Linux isso não desbloqueia um `recvfrom`
# em curso. Sondando a cada 250 ms a parada é quase imediata, e o silêncio
# passa a ser medido pelo relógio, que é como devia ter sido desde o início.
POLL_TIMEOUT_S = 0.25

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
        # Sobrepõe o contador da base quando o núcleo passa o dele: os dois
        # precisam ser **o mesmo objeto**, senão a barra de status lê um e a
        # captura escreve no outro.
        if metrics is not None:
            self.metrics = metrics

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        #: O socket em uso, para que `stop()` possa fechá-lo de fora.
        #:
        #: Sem esta referência, parar era **pedir** para a thread parar e
        #: torcer: ela só percebia o evento ao sair do `recvfrom`, e se não
        #: saísse dentro do prazo do `join`, `stop()` voltava assim mesmo
        #: com a thread viva e a porta 33740 ainda ocupada. A captura
        #: seguinte então binda um segundo socket na mesma porta — o
        #: `SO_REUSEADDR` permite — e o sistema entrega cada pacote a **um**
        #: dos dois. Trocar o IP em Configurações passa por aqui, e cada
        #: troca podia deixar mais um ouvinte fantasma para trás.
        self._socket: socket.socket | None = None

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
        """Para a captura e **garante** que a porta foi liberada.

        Fecha o socket antes de esperar pela thread, em vez de só sinalizar.
        Um `recvfrom` bloqueado num socket fechado levanta na hora, então a
        thread sai imediatamente em vez de até 3 s depois — e, o que mais
        importa, a porta fica livre mesmo se a thread demorar.

        Sinalizar e esperar era o contrato antigo, e ele tinha uma brecha
        silenciosa: com o `join` estourando, `stop()` voltava com a thread
        viva e a porta ocupada. A captura seguinte bindava um segundo
        socket na mesma porta (o `SO_REUSEADDR` deixa), o sistema passava a
        entregar cada pacote a um dos dois, e o resultado era telemetria
        chegando na máquina sem chegar na tela. Trocar o IP em
        Configurações passa por aqui.
        """
        self._stop_event.set()

        # Fechar de fora é o que desbloqueia o `recvfrom`. A thread trata o
        # `OSError` resultante como parada — ver `_run`.
        sock = self._socket
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=SOCKET_TIMEOUT_S + 0.5)
            if thread.is_alive():
                # Não deveria acontecer com o socket fechado. Se acontecer,
                # é melhor deixar registrado do que seguir fingindo que a
                # captura parou.
                _log.error(
                    "a thread de captura não encerrou",
                    extra={"port": self._receive_port},
                )
        self._thread = None
        self._socket = None
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

        self._socket = sock
        self._emit_status(ConnectionState.CONNECTING)
        last_heartbeat = 0.0
        got_first_packet = False
        last_heartbeat_error: str | None = None
        # Silêncio medido pelo relógio, e não pelo timeout do socket: os dois
        # eram a mesma coisa e não deviam ser. Ver `POLL_TIMEOUT_S`.
        last_packet_at = time.time()
        reported_no_signal = False

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
                    if (
                        not reported_no_signal
                        and now - last_packet_at > SOCKET_TIMEOUT_S
                    ):
                        # Uma vez por episódio de silêncio, não a cada 250 ms:
                        # repetir o mesmo estado quatro vezes por segundo é
                        # ruído que a interface teria de filtrar.
                        self._emit_status(ConnectionState.NO_SIGNAL)
                        reported_no_signal = True
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise

                # Qualquer pacote conta para o relógio do silêncio: silêncio é
                # ausência **de tráfego**, e lixo na porta não é ausência.
                last_packet_at = time.time()
                self.metrics.record_packet(len(data))

                decoded = salsa20_decode(data)
                if decoded is None:
                    # Pacote de outra origem na mesma porta, ou corrompido.
                    self.metrics.record_invalid()
                    continue

                # Voltar a receber é um **evento**, e precisa ser anunciado.
                #
                # Antes, `RECEIVING` só era emitido na guarda de primeiro
                # pacote; o retorno do silêncio limpava a bandeira interna e não
                # avisava ninguém. Bastavam três segundos calados — um menu do
                # jogo, uma tela de carregamento, um soluço de Wi-Fi — para o
                # botão ficar em SEM SINAL **para sempre**, com a telemetria
                # chegando normalmente atrás. O indicador mentia, e mentir sobre
                # a conexão é o pior lugar para mentir: é o primeiro lugar em
                # que se olha quando alguma coisa parece errada.
                if not got_first_packet or reported_no_signal:
                    primeiro = not got_first_packet
                    got_first_packet = True
                    reported_no_signal = False
                    self._emit_status(ConnectionState.RECEIVING)
                    _log.info(
                        "primeiro pacote recebido" if primeiro else "sinal recuperado",
                        extra={"ps_ip": self._ps_ip},
                    )

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
            # falharia com "endereço em uso". Fechar duas vezes (aqui e no
            # `stop()`) é inofensivo.
            self._socket = None
            sock.close()

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self._receive_port))
        sock.settimeout(POLL_TIMEOUT_S)
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
