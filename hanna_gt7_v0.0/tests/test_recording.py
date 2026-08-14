"""
Testes de gravação e replay.

A propriedade central: uma sessão gravada e reproduzida entrega **os mesmos
quadros** que a original. É isso que permite ao §40 funcionar — a aplicação não
precisa saber se a fonte é ao vivo ou replay porque os dois produzem dados
indistinguíveis.
"""

from __future__ import annotations

import struct
import threading
import time
from pathlib import Path

import pytest

from gt7core.telemetry.protocol import TelemetryFrame
from gt7core.telemetry.recording import (
    MAGIC,
    RecordingFormatError,
    ReplayTelemetrySource,
    SessionRecorder,
    read_recording,
)
from gt7core.telemetry.sources.base import ConnectionState
from gt7core.telemetry.sources.mock import synthetic_lap


@pytest.fixture
def recording_path(tmp_path: Path) -> Path:
    """Uma volta sintética já gravada em disco."""
    path = tmp_path / "sessao.gt7rec"
    with SessionRecorder(path) as recorder:
        for frame in synthetic_lap(lap_time_ms=3_000):
            recorder.record(frame)
    return path


class TestIdaEVolta:
    def test_grava_e_le_a_mesma_quantidade(self, tmp_path: Path) -> None:
        frames = list(synthetic_lap(lap_time_ms=2_000))
        path = tmp_path / "s.gt7rec"

        with SessionRecorder(path) as recorder:
            for frame in frames:
                recorder.record(frame)

        assert recorder.frames_written == len(frames)
        assert len(list(read_recording(path))) == len(frames)

    def test_quadros_sobrevivem_identicos(self, tmp_path: Path) -> None:
        """A propriedade que sustenta o replay: nada se perde no caminho."""
        original = list(synthetic_lap(lap_time_ms=2_000))
        path = tmp_path / "s.gt7rec"

        with SessionRecorder(path) as recorder:
            for frame in original:
                recorder.record(frame)

        restored = [frame for _offset, frame in read_recording(path)]

        assert restored == original

    def test_tipos_inteiros_voltam_como_inteiros(self, recording_path: Path) -> None:
        """Os campos são gravados como double; a leitura reconverte. Sem isso,
        `gear` voltaria 4.0 e quebraria formatação e comparação."""
        _offset, frame = next(iter(read_recording(recording_path)))

        assert isinstance(frame.gear, int)
        assert isinstance(frame.lap_count, int)
        assert isinstance(frame.flags, int)
        assert isinstance(frame.car_id, int)
        assert isinstance(frame.speed_kmh, float)

    def test_timestamps_sao_crescentes(self, recording_path: Path) -> None:
        offsets = [offset for offset, _frame in read_recording(recording_path)]

        assert offsets[0] == pytest.approx(0.0, abs=1e-6)
        assert all(b >= a for a, b in zip(offsets, offsets[1:], strict=False))

    def test_arquivo_vazio_e_valido(self, tmp_path: Path) -> None:
        """Gravação sem nenhum quadro: cabeçalho válido, zero registros."""
        path = tmp_path / "vazia.gt7rec"
        with SessionRecorder(path):
            pass

        assert list(read_recording(path)) == []


class TestFormatoInvalido:
    """§38: arquivo corrompido, truncado, de outra origem."""

    def test_arquivo_que_nao_e_gravacao(self, tmp_path: Path) -> None:
        path = tmp_path / "qualquer.bin"
        path.write_bytes(b"isto nao e uma gravacao do gt7" * 4)

        with pytest.raises(RecordingFormatError, match="não é um arquivo"):
            list(read_recording(path))

    def test_cabecalho_truncado(self, tmp_path: Path) -> None:
        path = tmp_path / "curta.gt7rec"
        path.write_bytes(MAGIC[:3])

        with pytest.raises(RecordingFormatError, match="truncado"):
            list(read_recording(path))

    def test_versao_incompativel(self, tmp_path: Path) -> None:
        """Um arquivo de versão futura precisa falhar com mensagem clara, não
        ler lixo e produzir telemetria plausível mas errada."""
        path = tmp_path / "futura.gt7rec"
        path.write_bytes(struct.pack("<6sHHII", MAGIC, 99, 41, 60, 0))

        with pytest.raises(RecordingFormatError, match="versão 99"):
            list(read_recording(path))

    def test_contagem_de_campos_diferente(self, tmp_path: Path) -> None:
        """O protocolo ganhou ou perdeu campo desde a gravação."""
        path = tmp_path / "campos.gt7rec"
        path.write_bytes(struct.pack("<6sHHII", MAGIC, 1, 7, 60, 0))

        with pytest.raises(RecordingFormatError, match="7 campos"):
            list(read_recording(path))

    def test_registro_final_truncado_e_ignorado(self, tmp_path: Path) -> None:
        """Gravação interrompida por queda: o que veio antes continua válido."""
        path = tmp_path / "interrompida.gt7rec"
        with SessionRecorder(path) as recorder:
            for frame in synthetic_lap(lap_time_ms=1_000):
                recorder.record(frame)

        complete = len(list(read_recording(path)))
        with path.open("ab") as handle:
            handle.write(b"\x00" * 40)  # meio registro

        assert len(list(read_recording(path))) == complete


class TestReplayTelemetrySource:
    def test_reproduz_todos_os_quadros(self, recording_path: Path) -> None:
        source = ReplayTelemetrySource(recording_path, speed_multiplier=500.0)
        received: list[TelemetryFrame] = []
        lock = threading.Lock()

        def collect(frame: TelemetryFrame) -> None:
            with lock:
                received.append(frame)

        source.on_frame(collect)
        expected = len(list(read_recording(recording_path)))

        source.start()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with lock:
                if len(received) >= expected:
                    break
            time.sleep(0.02)
        source.stop()

        assert len(received) == expected

    def test_replay_entrega_os_mesmos_dados_do_original(
        self, tmp_path: Path
    ) -> None:
        """O teste que fecha o §40: ao vivo e replay são indistinguíveis."""
        original = list(synthetic_lap(lap_time_ms=1_000))
        path = tmp_path / "s.gt7rec"
        with SessionRecorder(path) as recorder:
            for frame in original:
                recorder.record(frame)

        source = ReplayTelemetrySource(path, speed_multiplier=1000.0)
        received: list[TelemetryFrame] = []
        lock = threading.Lock()
        source.on_frame(lambda f: (lock.acquire(), received.append(f), lock.release()))

        source.start()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and len(received) < len(original):
            time.sleep(0.02)
        source.stop()

        assert received == original

    def test_anuncia_estados_de_conexao(self, recording_path: Path) -> None:
        source = ReplayTelemetrySource(recording_path, speed_multiplier=1000.0)
        states: list[ConnectionState] = []
        source.on_status(lambda state, _msg: states.append(state))

        source.start()
        time.sleep(0.5)
        source.stop()

        assert ConnectionState.RECEIVING in states
        assert states[-1] == ConnectionState.DISCONNECTED

    def test_arquivo_inexistente_vira_estado_de_erro(self, tmp_path: Path) -> None:
        """§41: um replay quebrado não pode derrubar a aplicação."""
        source = ReplayTelemetrySource(tmp_path / "nao-existe.gt7rec")
        states: list[tuple[ConnectionState, str]] = []
        source.on_status(lambda state, msg: states.append((state, msg)))

        source.start()
        time.sleep(0.3)
        source.stop()

        assert any(state == ConnectionState.ERROR for state, _msg in states)

    def test_stop_interrompe_no_meio(self, recording_path: Path) -> None:
        source = ReplayTelemetrySource(recording_path, speed_multiplier=1.0)
        source.start()
        time.sleep(0.1)
        source.stop()

        assert source.is_running is False

    def test_satisfaz_o_mesmo_contrato_da_fonte_real(self, recording_path: Path) -> None:
        """É o contrato compartilhado que torna o §40 possível sem `if replay`."""
        from gt7core.telemetry.sources.base import TelemetrySource

        source = ReplayTelemetrySource(recording_path)

        assert isinstance(source, TelemetrySource)
        source.stop()  # idempotente mesmo sem start


class TestPipelineComReplay:
    def test_replay_alimenta_o_motor_como_o_ao_vivo(self, tmp_path: Path) -> None:
        """Gravar uma sessão, reproduzir e obter as mesmas voltas."""
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import LapBoundaryDetected, TelemetryEngine
        from gt7core.telemetry.sources.mock import synthetic_session

        path = tmp_path / "sessao.gt7rec"
        with SessionRecorder(path) as recorder:
            for frame in synthetic_session(lap_count=3):
                recorder.record(frame)

        bus = EventBus()
        engine = TelemetryEngine(bus)
        laps: list[LapBoundaryDetected] = []
        bus.subscribe(LapBoundaryDetected, laps.append)

        for _offset, frame in read_recording(path):
            engine.on_frame(frame)

        assert len(laps) == 2  # a última só fecha quando o contador vira
        assert all(lap.lap_time_ms > 0 for lap in laps)
        assert all(len(lap.points) > 100 for lap in laps)
