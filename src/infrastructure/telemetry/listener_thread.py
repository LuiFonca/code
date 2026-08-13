"""
Captura UDP da telemetria do GT7.

Duas classes, de propósito:

- `_ListenerThread` — a QThread que fala com o socket.
- `Gt7TelemetrySource` — o adaptador que implementa `TelemetrySource` e
  encapsula a thread.

Por que composição e não `class Gt7TelemetrySource(QThread, TelemetrySource)`:
PySide6 não permite herdar de duas classes Qt ao mesmo tempo, e `TelemetrySource`
é QObject (precisa expor `Signal`). Além da restrição técnica, a separação é
melhor: o ciclo de vida da thread fica escondido atrás de `start()`/`stop()`, e
quem consome a interface não precisa saber que existe uma thread.
"""

import socket
import struct
import time

from PySide6.QtCore import QThread, Signal

from ...domain.config import AppConfig
from ...domain.interfaces.telemetry_source import TelemetrySource
from .gt7_protocol import TelemetryFrame, salsa20_decode

SEND_PORT = 33739
RECEIVE_PORT = 33740

# O PS5 para de transmitir se não receber um "toque" periódico.
HEARTBEAT_INTERVAL = 10

# Antes do primeiro pacote, o toque é bem mais frequente. Dois motivos:
#
# 1. `EHOSTUNREACH` numa rede local costuma ser **transitório**: o kernel
#    responde isso enquanto a resolução ARP do endereço ainda está em curso.
#    Uma segunda tentativa logo depois normalmente passa. Com 10s de espera, a
#    conexão demorava dez segundos para se estabelecer — ou parecia falha.
# 2. Se o console foi ligado depois do app, ele só começa a transmitir a partir
#    do primeiro toque que receber.
HEARTBEAT_INTERVAL_INITIAL = 1.0

# Tempo sem nenhum pacote antes de reportar "sem_sinal". Mais grosseiro que o
# watchdog da camada de apresentação (que vigia frames válidos, ~1s): aqui é
# ausência total de tráfego.
SOCKET_TIMEOUT = 3


class _ListenerThread(QThread):
    """Escuta a porta UDP e emite um `TelemetryFrame` por pacote válido.

    Tudo aqui roda fora da thread da UI. A comunicação é exclusivamente por
    sinal — nenhum widget é tocado a partir daqui.
    """

    frame_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, ps_ip: str, heartbeat_interval: float = HEARTBEAT_INTERVAL,
                 parent=None):
        super().__init__(parent)
        self.ps_ip = ps_ip
        self._heartbeat_interval = heartbeat_interval
        self._running = False

    def start(self, *args, **kwargs) -> None:
        """Arma a flag ANTES de disparar a thread.

        `run()` não pode ser quem liga `_running`: `QThread.start()` retorna
        imediatamente, e um `stop()` que chegue antes de `run()` começar seria
        desfeito na primeira linha dela. O resultado é uma thread órfã presa no
        laço, segurando a porta 33740 — e a próxima conexão falha com
        "endereço em uso" sem explicação.
        """
        self._running = True
        super().start(*args, **kwargs)

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", RECEIVE_PORT))
            sock.settimeout(SOCKET_TIMEOUT)
        except OSError as e:
            self.error_occurred.emit(f"Não foi possível abrir a porta de captura: {e}")
            return

        self.status_changed.emit("conectando")
        last_heartbeat = 0.0
        got_first_packet = False
        last_heartbeat_error: str | None = None

        try:
            while self._running:
                now = time.time()
                interval = (
                    self._heartbeat_interval
                    if got_first_packet
                    else HEARTBEAT_INTERVAL_INITIAL
                )
                if now - last_heartbeat > interval:
                    try:
                        sock.sendto(b"A", (self.ps_ip, SEND_PORT))
                        if last_heartbeat_error is not None:
                            # O toque voltou a passar depois de falhar (ARP
                            # resolvido, cabo reconectado...). Avisa para a
                            # interface poder limpar o alerta anterior — sem
                            # isto, um erro transitório ficaria na tela para
                            # sempre, mesmo com tudo já funcionando.
                            last_heartbeat_error = None
                            self.status_changed.emit("conectando")
                    except OSError as e:
                        # Só reporta quando a mensagem muda. Sem isso, um
                        # console desligado encheria a interface com o mesmo
                        # erro repetido indefinidamente.
                        message = self._describe_send_error(e)
                        if message != last_heartbeat_error:
                            last_heartbeat_error = message
                            self.error_occurred.emit(message)
                    last_heartbeat = now

                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    self.status_changed.emit("sem_sinal")
                    continue

                decoded = salsa20_decode(data)
                if decoded is None:
                    # Pacote de outra origem na mesma porta, ou corrompido.
                    continue

                if not got_first_packet:
                    got_first_packet = True
                    self.status_changed.emit("recebendo")

                try:
                    frame = TelemetryFrame.from_bytes(decoded)
                except struct.error:
                    # Pacote curto/truncado: descarta e segue.
                    continue

                self.frame_received.emit(frame)
        finally:
            # `finally` garante que o socket feche mesmo se a decodificação
            # levantar algo inesperado — senão a porta ficaria presa até o
            # processo morrer, e a reconexão falharia com "endereço em uso".
            sock.close()

    def _describe_send_error(self, error: OSError) -> str:
        """Traduz a falha de envio em algo acionável.

        A mensagem crua do sistema ("[Errno 65] No route to host") não diz o
        que fazer nem para qual endereço a tentativa foi. Os números mudam
        entre sistemas — EHOSTUNREACH é 65 no macOS e 113 no Linux — então a
        comparação usa as constantes do módulo `errno`, não literais.
        """
        import errno

        ip = self.ps_ip
        if error.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return (
                f"Não foi possível alcançar o PlayStation em {ip} (sem rota até o "
                f"host). Confira se o IP está correto e se o console e este "
                f"computador estão na mesma rede."
            )
        if error.errno == errno.ECONNREFUSED:
            return (
                f"O PlayStation em {ip} recusou a conexão. Verifique se o GT7 "
                f"está aberto numa sessão."
            )
        if error.errno in (errno.EADDRNOTAVAIL, errno.EINVAL):
            return f"Endereço inválido: {ip!r}. Confira o IP digitado."
        return f"Falha ao contatar o PlayStation em {ip}: {error}"

    def stop(self) -> None:
        self._running = False
        # Espera um pouco mais que o timeout do socket: a thread pode estar
        # bloqueada em `recvfrom` e só perceber o pedido de parada ao expirar.
        if not self.wait(SOCKET_TIMEOUT * 1000 + 500):
            # Não terminou no prazo. Melhor relatar do que seguir em frente
            # fingindo que encerrou — uma thread presa aqui é justamente a que
            # produz "endereço em uso" na próxima conexão.
            self.error_occurred.emit(
                "A captura não encerrou no tempo esperado. Se a próxima conexão "
                "falhar com 'endereço em uso', reinicie o aplicativo."
            )


class Gt7TelemetrySource(TelemetrySource):
    """Implementação de `TelemetrySource` para o GT7 via UDP."""

    def __init__(self, ps_ip: str | None = None, config: AppConfig | None = None,
                 parent=None):
        super().__init__(parent)
        self._config = config or AppConfig()
        self._ps_ip = ps_ip or self._config.ps_ip
        self._thread: _ListenerThread | None = None

    @property
    def ps_ip(self) -> str:
        return self._ps_ip

    def set_ps_ip(self, ps_ip: str) -> None:
        """Troca o IP alvo. Só vale a partir do próximo `start()` — trocar o
        destino de uma captura em andamento misturaria dados de dois consoles."""
        self._ps_ip = ps_ip

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        if self.is_running:
            return  # idempotente, conforme o contrato da interface

        self._thread = _ListenerThread(
            self._ps_ip, heartbeat_interval=self._config.heartbeat_interval_s
        )
        # Reencaminhamento sinal-a-sinal: o Qt repassa a emissão sem handler
        # intermediário, preservando a thread de origem para que a conexão
        # enfileirada aconteça no assinante final.
        self._thread.frame_received.connect(self.telemetry_stream)
        self._thread.status_changed.connect(self.status_changed)
        self._thread.error_occurred.connect(self.error_occurred)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        thread = self._thread
        thread.stop()
        # A referência só é solta depois da parada de fato. Descartá-la antes
        # deixaria o objeto QThread sem dono enquanto ainda roda, e o Qt avisa
        # "Destroyed while thread is still running" — quando avisa.
        self._thread = None
        if thread.isRunning():
            thread.wait(500)
        self.status_changed.emit("desconectado")
