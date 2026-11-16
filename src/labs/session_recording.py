"""
Gravação da sessão bruta e reprodução offline.

Hoje o app guarda voltas já interpretadas. Guardar os **pacotes** resolve
outro problema: reproduzir uma sessão real sem o console ligado — para testar
uma mudança com dados de verdade, ou para investigar um defeito que só
apareceu na pista.

Formato do arquivo
------------------
Cabeçalho de 8 bytes (`GT7REC` + versão), depois registros
`<instante em ms: uint32><tamanho: uint16><pacote>`. O pacote é o decifrado,
antes do parser: se a leitura de algum campo for corrigida depois, a gravação
antiga continua valendo e passa a ser lida do jeito novo.

O instante é relativo ao primeiro pacote. Guardar a hora do relógio não
serviria para nada na reprodução e ainda revelaria quando a sessão aconteceu.
"""

import struct
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from ..domain.interfaces.telemetry_source import TelemetrySource
from ..infrastructure.telemetry.gt7_protocol import TelemetryFrame

MAGIC = b"GT7REC"
FORMAT_VERSION = 1
_HEADER = struct.Struct("<6sH")
_RECORD = struct.Struct("<IH")


class RecordingError(ValueError):
    """O arquivo não é uma gravação legível por esta versão."""


class SessionRecorder:
    """Escreve os pacotes recebidos num arquivo.

    Liga-se ao `raw_packet_stream` da fonte:

        recorder = SessionRecorder(caminho)
        source.raw_packet_stream.connect(recorder.write)
        ...
        recorder.close()
    """

    def __init__(self, path: Path | str, clock=time.monotonic):
        self._path = Path(path)
        self._clock = clock
        self._start: float | None = None
        self.packets = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("wb")
        self._file.write(_HEADER.pack(MAGIC, FORMAT_VERSION))

    def write(self, packet: bytes) -> None:
        if self._file is None or len(packet) > 0xFFFF:
            return
        agora = self._clock()
        if self._start is None:
            self._start = agora
        instante_ms = int((agora - self._start) * 1000) & 0xFFFFFFFF
        self._file.write(_RECORD.pack(instante_ms, len(packet)))
        self._file.write(packet)
        self.packets += 1

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def read_recording(path: Path | str) -> list[tuple[int, bytes]]:
    """Lê uma gravação inteira: lista de `(instante_ms, pacote)`.

    Um registro cortado no fim (app fechado no meio da escrita) é descartado
    sem erro: perder o último pacote não justifica recusar a sessão inteira.
    """
    dados = Path(path).read_bytes()
    if len(dados) < _HEADER.size:
        raise RecordingError("Arquivo curto demais para ser uma gravação.")
    magic, versao = _HEADER.unpack_from(dados, 0)
    if magic != MAGIC:
        raise RecordingError("Este arquivo não é uma gravação de sessão.")
    if versao > FORMAT_VERSION:
        raise RecordingError(
            f"Gravação em formato mais novo (versão {versao}); esta versão do "
            f"app lê até a {FORMAT_VERSION}."
        )

    registros = []
    pos = _HEADER.size
    while pos + _RECORD.size <= len(dados):
        instante, tamanho = _RECORD.unpack_from(dados, pos)
        pos += _RECORD.size
        if pos + tamanho > len(dados):
            break
        registros.append((instante, dados[pos:pos + tamanho]))
        pos += tamanho
    return registros


class ReplayTelemetrySource(TelemetrySource):
    """Fonte de telemetria que lê de uma gravação em vez da rede.

    Satisfaz o mesmo contrato do listener UDP, então o resto do app não nota
    diferença — é só trocar a fonte no composition root.

    Dois modos:

    - `start()` reproduz no ritmo original, por temporizador;
    - `play_all()` despeja tudo de uma vez, que é o que um teste quer.
    """

    def __init__(self, path: Path | str, speed: float = 1.0, parent=None):
        super().__init__(parent)
        self._records = read_recording(path)
        self._speed = max(speed, 0.01)
        self._index = 0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emit_due)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def packet_count(self) -> int:
        return len(self._records)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._index = 0
        self.status_changed.emit("recebendo" if self._records else "sem_sinal")
        self._schedule_next()

    def stop(self) -> None:
        self._timer.stop()
        if self._running:
            self._running = False
            self.status_changed.emit("desconectado")

    def play_all(self) -> int:
        """Emite todos os pacotes agora, ignorando os intervalos."""
        emitidos = 0
        for _instante, pacote in self._records:
            if self._emit(pacote):
                emitidos += 1
        return emitidos

    def _emit(self, pacote: bytes) -> bool:
        try:
            self.telemetry_stream.emit(TelemetryFrame.from_bytes(pacote))
            return True
        except struct.error:
            # Pacote truncado na gravação: pula, como o listener faria.
            return False

    def _schedule_next(self) -> None:
        if not self._running or self._index >= len(self._records):
            if self._running:
                self.stop()
            return
        if self._index == 0:
            espera = 0
        else:
            espera = self._records[self._index][0] - self._records[self._index - 1][0]
        self._timer.start(max(int(espera / self._speed), 0))

    def _emit_due(self) -> None:
        if not self._running:
            return
        self._emit(self._records[self._index][1])
        self._index += 1
        self._schedule_next()
