"""
Gravação e reprodução de sessões — §40 do briefing.

O briefing pede que a aplicação **não saiba** se está recebendo ao vivo ou em
replay. Como `ReplayTelemetrySource` satisfaz o mesmo contrato que a fonte UDP e
emite o mesmo `TelemetryFrame`, isso sai de graça: nenhum `if modo_replay` em
lugar nenhum do sistema.

Formato do arquivo (`.gt7rec`)
------------------------------
Cabeçalho de 16 bytes, depois um registro por quadro::

    cabeçalho   b"GT7REC" | versão (uint16) | contagem de campos (uint16)
                | taxa nominal em Hz (uint32) | reservado (uint32)
    registro    timestamp relativo em segundos (double) | campos do quadro

Os campos são gravados na ordem declarada em `TelemetryFrame`, com o formato
derivado do próprio dataclass — assim um campo novo no protocolo não sai de
sincronia com o gravador em silêncio. A versão no cabeçalho permite recusar um
arquivo antigo com mensagem clara em vez de ler lixo.

Grava-se o **quadro decodificado**, não o pacote cifrado: o replay não depende
da chave nem do decodificador, e um arquivo gravado continua legível se o
protocolo do jogo mudar.
"""

from __future__ import annotations

import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import BinaryIO

from ..observability.logging import get_logger
from .protocol import TelemetryFrame
from .sources.base import ConnectionState, TelemetrySource

_log = get_logger(__name__)

MAGIC = b"GT7REC"
FORMAT_VERSION = 1
HEADER_STRUCT = struct.Struct("<6sHHII")

_FRAME_FIELDS = tuple(f.name for f in fields(TelemetryFrame))
# `d` para tudo: evita perda de precisão nos floats e cobre a faixa dos inteiros
# sem um mapa de tipos que precisaria ser mantido em sincronia à mão.
_RECORD_STRUCT = struct.Struct("<d" + "d" * len(_FRAME_FIELDS))


class RecordingFormatError(Exception):
    """Arquivo de gravação inválido, truncado ou de versão incompatível."""


class SessionRecorder:
    """Grava quadros num arquivo `.gt7rec`.

    Usa-se como context manager e liga-se a qualquer fonte::

        with SessionRecorder("sessao.gt7rec") as recorder:
            source.on_frame(recorder.record)
            source.start()

    A escrita é bufferizada pelo próprio arquivo; o `flush` acontece no
    fechamento. Isso mantém o custo por quadro baixo no caminho quente — é o
    mesmo motivo pelo qual a persistência de volta acontece só no fechamento.
    """

    def __init__(self, path: str | Path, *, sample_rate_hz: int = 60) -> None:
        self.path = Path(path)
        self._sample_rate_hz = sample_rate_hz
        self._handle: BinaryIO | None = None
        self._started_at: float | None = None
        self._lock = threading.Lock()
        self.frames_written = 0

    def __enter__(self) -> SessionRecorder:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("wb")
        self._handle.write(
            HEADER_STRUCT.pack(
                MAGIC, FORMAT_VERSION, len(_FRAME_FIELDS), self._sample_rate_hz, 0
            )
        )
        self._started_at = None
        self.frames_written = 0

    def record(self, frame: TelemetryFrame) -> None:
        """Grava um quadro. Seguro para chamar da thread de rede."""
        with self._lock:
            if self._handle is None:
                return
            now = time.monotonic()
            if self._started_at is None:
                self._started_at = now
            values = [getattr(frame, name) for name in _FRAME_FIELDS]
            self._handle.write(_RECORD_STRUCT.pack(now - self._started_at, *values))
            self.frames_written += 1

    def close(self) -> None:
        with self._lock:
            if self._handle is None:
                return
            self._handle.close()
            self._handle = None
        _log.info(
            "gravação encerrada",
            extra={"path": str(self.path), "frames": self.frames_written},
        )


def read_recording(path: str | Path) -> Iterator[tuple[float, TelemetryFrame]]:
    """Lê um `.gt7rec`, devolvendo pares (segundos desde o início, quadro).

    Gerador em vez de lista: uma sessão de uma hora a 60 Hz tem 216 mil quadros,
    e carregar tudo na memória para reproduzir em ordem seria desperdício.
    """
    file_path = Path(path)
    with file_path.open("rb") as handle:
        header = handle.read(HEADER_STRUCT.size)
        if len(header) < HEADER_STRUCT.size:
            raise RecordingFormatError(f"{file_path}: arquivo truncado no cabeçalho")

        magic, version, field_count, _rate, _reserved = HEADER_STRUCT.unpack(header)
        if magic != MAGIC:
            raise RecordingFormatError(f"{file_path}: não é um arquivo .gt7rec")
        if version != FORMAT_VERSION:
            raise RecordingFormatError(
                f"{file_path}: versão {version} não suportada (esperada {FORMAT_VERSION})"
            )
        if field_count != len(_FRAME_FIELDS):
            # O protocolo ganhou ou perdeu campo desde a gravação. Recusar com
            # mensagem clara é melhor que desalinhar os offsets e produzir
            # telemetria plausível mas errada.
            raise RecordingFormatError(
                f"{file_path}: gravado com {field_count} campos, "
                f"esta versão espera {len(_FRAME_FIELDS)}"
            )

        while True:
            chunk = handle.read(_RECORD_STRUCT.size)
            if not chunk:
                return
            if len(chunk) < _RECORD_STRUCT.size:
                # Gravação interrompida (queda de energia, kill -9). O que veio
                # antes é válido e é devolvido; o rabo incompleto é ignorado.
                _log.warning(
                    "registro final truncado — ignorado", extra={"path": str(file_path)}
                )
                return

            unpacked = _RECORD_STRUCT.unpack(chunk)
            timestamp = unpacked[0]
            raw = unpacked[1:]

            # Os inteiros voltam como float (foram gravados como `d`); o
            # dataclass declara int, então a conversão é feita aqui.
            values: dict[str, object] = {}
            for name, value in zip(_FRAME_FIELDS, raw, strict=True):
                field_type = TelemetryFrame.__annotations__[name]
                values[name] = int(value) if field_type is int else value

            yield timestamp, TelemetryFrame(**values)  # type: ignore[arg-type]


class ReplayTelemetrySource(TelemetrySource):
    """Reproduz uma sessão gravada como se fosse ao vivo.

    Respeita os intervalos originais entre quadros por padrão, o que faz o
    replay exercitar o sistema no mesmo ritmo da captura real — inclusive os
    watchdogs e as taxas de repintura. `speed_multiplier` acelera para análise
    (ou para um teste de carga que não precisa esperar em tempo real).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        speed_multiplier: float = 1.0,
        loop: bool = False,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self._speed_multiplier = max(0.01, speed_multiplier)
        self._loop = loop
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.frames_replayed = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ReplayTelemetrySource", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self._emit_status(ConnectionState.DISCONNECTED)

    def _run(self) -> None:
        self._emit_status(ConnectionState.CONNECTING)
        try:
            while not self._stop_event.is_set():
                self._replay_once()
                if not self._loop or self._stop_event.is_set():
                    break
        except (OSError, RecordingFormatError) as error:
            _log.error("replay falhou", extra={"path": str(self.path)})
            self._emit_status(ConnectionState.ERROR, str(error))
            return
        self._emit_status(ConnectionState.DISCONNECTED)

    def _replay_once(self) -> None:
        started_at = time.monotonic()
        announced = False

        for offset_s, frame in read_recording(self.path):
            if self._stop_event.is_set():
                return

            if not announced:
                announced = True
                self._emit_status(ConnectionState.RECEIVING)

            # Agenda por horário absoluto a partir do início: o custo de
            # processar cada quadro não acumula atraso ao longo da sessão.
            target = started_at + offset_s / self._speed_multiplier
            delay = target - time.monotonic()
            if delay > 0:
                self._stop_event.wait(delay)

            self._emit_frame(frame)
            self.frames_replayed += 1
