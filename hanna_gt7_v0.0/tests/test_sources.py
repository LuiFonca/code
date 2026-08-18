"""
Testes do contrato de fonte de telemetria.

O contrato é o que torna replay (§40) e outros simuladores (§42) possíveis sem
código extra: se `MockTelemetrySource` satisfaz a mesma interface que a fonte
UDP real, a aplicação não distingue as duas. Os testes abaixo fixam as
propriedades do contrato — idempotência, isolamento de callback, ciclo de vida.
"""

from __future__ import annotations

import threading
import time

import pytest

from gt7core.telemetry.protocol import TelemetryFrame
from gt7core.telemetry.sources.base import ConnectionState, TelemetrySource
from gt7core.telemetry.sources.mock import MockTelemetrySource, synthetic_lap


class RecordingSource(TelemetrySource):
    """Fonte mínima para exercitar só o contrato da base."""

    def __init__(self) -> None:
        super().__init__()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def emit(self, frame: TelemetryFrame) -> None:
        self._emit_frame(frame)

    def emit_status(self, state: ConnectionState, message: str = "") -> None:
        self._emit_status(state, message)


class TestContratoDaBase:
    def test_callback_recebe_o_quadro(self) -> None:
        source = RecordingSource()
        received: list[TelemetryFrame] = []
        source.on_frame(received.append)

        frame = next(iter(synthetic_lap()))
        source.emit(frame)

        assert received == [frame]

    def test_registrar_duas_vezes_nao_duplica(self) -> None:
        source = RecordingSource()
        received: list[TelemetryFrame] = []
        source.on_frame(received.append)
        source.on_frame(received.append)

        source.emit(next(iter(synthetic_lap())))

        assert len(received) == 1

    def test_callback_que_levanta_nao_derruba_a_captura(self) -> None:
        """§41: um bug na interface não pode matar a gravação da volta."""
        source = RecordingSource()
        survivors: list[TelemetryFrame] = []

        def exploding(_: TelemetryFrame) -> None:
            raise RuntimeError("consumidor quebrado")

        source.on_frame(exploding)
        source.on_frame(survivors.append)

        source.emit(next(iter(synthetic_lap())))  # não propaga

        assert len(survivors) == 1

    def test_status_chega_com_estado_tipado(self) -> None:
        """P12: estado é enum, não string mágica em português."""
        source = RecordingSource()
        states: list[tuple[ConnectionState, str]] = []
        source.on_status(lambda state, msg: states.append((state, msg)))

        source.emit_status(ConnectionState.NO_SIGNAL, "sem pacotes")

        assert states == [(ConnectionState.NO_SIGNAL, "sem pacotes")]

    def test_estado_serializa_como_string(self) -> None:
        """Herdar de str mantém log, JSON e banco funcionando sem conversão."""
        assert ConnectionState.RECEIVING == "receiving"
        assert f"{ConnectionState.RECEIVING}" == "receiving"  # StrEnum
        assert ConnectionState.RECEIVING.value == "receiving"


class TestMockTelemetrySource:
    def test_produz_quadros_numa_thread(self) -> None:
        source = MockTelemetrySource(lap_count=1, speed_multiplier=200.0)
        received: list[TelemetryFrame] = []
        lock = threading.Lock()

        def collect(frame: TelemetryFrame) -> None:
            with lock:
                received.append(frame)

        source.on_frame(collect)
        source.start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with lock:
                if len(received) > 100:
                    break
            time.sleep(0.02)
        source.stop()

        assert len(received) > 100
        assert all(isinstance(frame, TelemetryFrame) for frame in received)

    def test_start_e_idempotente(self) -> None:
        """Chamar duas vezes não abre duas capturas — parte do contrato."""
        source = MockTelemetrySource(lap_count=1, speed_multiplier=100.0)
        source.start()
        first_thread = source._thread  # noqa: SLF001
        source.start()

        assert source._thread is first_thread  # noqa: SLF001
        source.stop()

    def test_stop_sem_start_e_seguro(self) -> None:
        MockTelemetrySource().stop()  # não deve levantar

    def test_stop_encerra_a_thread(self) -> None:
        source = MockTelemetrySource(lap_count=50, speed_multiplier=50.0)
        source.start()
        assert source.is_running is True

        source.stop()

        assert source.is_running is False

    def test_stop_anuncia_desconexao(self) -> None:
        source = MockTelemetrySource(lap_count=1, speed_multiplier=100.0)
        states: list[ConnectionState] = []
        source.on_status(lambda state, _msg: states.append(state))

        source.start()
        time.sleep(0.1)
        source.stop()

        assert ConnectionState.RECEIVING in states
        assert states[-1] == ConnectionState.DISCONNECTED


class TestGeradorSintetico:
    def test_e_deterministico(self) -> None:
        """Mesma entrada, mesma saída: é o que permite testar sem flake."""
        first = [f.speed_kmh for f in synthetic_lap(lap_time_ms=10_000)]
        second = [f.speed_kmh for f in synthetic_lap(lap_time_ms=10_000)]

        assert first == second

    def test_distancia_fecha_com_o_comprimento_da_pista(self) -> None:
        """O gerador é fisicamente coerente: integrar a velocidade dá o
        comprimento configurado. Sem isso, os testes de alinhamento por
        distância estariam medindo uma pista imaginária."""
        frames = list(synthetic_lap(lap_time_ms=90_000, track_length_m=5000.0))

        distance = 0.0
        previous_speed = previous_ms = None
        for frame in frames:
            speed = frame.speed_kmh / 3.6
            if previous_speed is not None:
                # O tempo agora vem do tick, como no motor: o gerador não
                # inventa mais um campo que o GT7 não transmite.
                dt = (frame.packet_id - previous_ms) / 60.0
                distance += (previous_speed + speed) / 2 * dt
            previous_speed, previous_ms = speed, frame.packet_id

        assert distance == pytest.approx(5000.0, rel=0.01)

    def test_contadores_de_volta_sobem(self) -> None:
        from gt7core.telemetry.sources.mock import synthetic_session

        counters = sorted({f.lap_count for f in synthetic_session(lap_count=4)})
        assert counters == [1, 2, 3, 4]

    def test_tempos_de_volta_variam_entre_voltas(self) -> None:
        """Sem variação de ritmo, "melhor volta" e delta não teriam o que
        comparar e os testes de analytics seriam vácuos."""
        from gt7core.telemetry.sources.mock import synthetic_session

        # Quadros por volta: o tick é global e monotônico, então a contagem
        # de quadros de cada volta é o que revela a diferença de ritmo.
        frames_por_volta: dict[int, int] = {}
        for frame in synthetic_session(lap_count=4):
            frames_por_volta[frame.lap_count] = (
                frames_por_volta.get(frame.lap_count, 0) + 1
            )

        assert len(set(frames_por_volta.values())) > 1
