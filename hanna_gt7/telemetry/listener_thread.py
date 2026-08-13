"""
Thread de captura de telemetria.
Roda separada da interface para não travar a GUI enquanto espera pacotes
de rede. Comunica com a interface exclusivamente via sinais Qt.
"""

import socket
import struct
import time

from PySide6.QtCore import QThread, Signal

from .gt7_protocol import salsa20_decode, TelemetryFrame

SEND_PORT = 33739
RECEIVE_PORT = 33740
HEARTBEAT_INTERVAL = 10
SOCKET_TIMEOUT = 3


class TelemetryListenerThread(QThread):
    frame_received = Signal(object)   # emite um TelemetryFrame a cada pacote válido
    status_changed = Signal(str)      # "conectando", "recebendo", "sem_sinal", "erro"
    error_occurred = Signal(str)

    def __init__(self, ps_ip: str, parent=None):
        super().__init__(parent)
        self.ps_ip = ps_ip
        self._running = False

    def run(self):
        self._running = True
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

        while self._running:
            now = time.time()
            if now - last_heartbeat > HEARTBEAT_INTERVAL:
                try:
                    sock.sendto(b"A", (self.ps_ip, SEND_PORT))
                except OSError as e:
                    self.error_occurred.emit(f"Erro ao enviar heartbeat: {e}")
                last_heartbeat = now

            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                self.status_changed.emit("sem_sinal")
                continue

            decoded = salsa20_decode(data)
            if decoded is None:
                continue

            if not got_first_packet:
                got_first_packet = True
                self.status_changed.emit("recebendo")

            try:
                frame = TelemetryFrame.from_bytes(decoded)
            except struct.error:
                continue

            self.frame_received.emit(frame)

        sock.close()

    def stop(self):
        self._running = False
        self.wait(SOCKET_TIMEOUT * 1000 + 500)
