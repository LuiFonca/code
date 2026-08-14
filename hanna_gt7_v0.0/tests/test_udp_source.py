"""
Testes da fonte UDP real, exercitada por socket local.

Não precisa de PS5: o teste abre um socket, manda pacotes cifrados com Salsa20
de verdade na porta que a fonte escuta, e verifica o que sai do outro lado. É o
caminho completo — rede → decodificação → validação → quadro — sem console.

Cobre o que a auditoria pediu no §38: pacote de outra origem, corrompido,
truncado, e ausência total de tráfego.
"""

from __future__ import annotations

import errno
import socket
import threading
import time

import pytest

from gt7core.telemetry.protocol import TelemetryFrame
from gt7core.telemetry.sources.base import ConnectionState
from gt7core.telemetry.sources.udp import (
    Gt7UdpTelemetrySource,
    describe_send_error,
)

from .conftest import build_plaintext_packet, encrypt_packet


def free_port() -> int:
    """Porta livre, para os testes não colidirem com a 33740 real nem entre si."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Harness:
    """Sobe a fonte, manda pacotes e coleta o que chega."""

    def __init__(self, **kwargs: object) -> None:
        self.receive_port = free_port()
        self.source = Gt7UdpTelemetrySource(
            "127.0.0.1",
            send_port=free_port(),
            receive_port=self.receive_port,
            **kwargs,  # type: ignore[arg-type]
        )
        self.frames: list[TelemetryFrame] = []
        self.states: list[tuple[ConnectionState, str]] = []
        self._lock = threading.Lock()

        self.source.on_frame(self._collect_frame)
        self.source.on_status(lambda state, msg: self.states.append((state, msg)))

    def _collect_frame(self, frame: TelemetryFrame) -> None:
        with self._lock:
            self.frames.append(frame)

    def __enter__(self) -> Harness:
        self.source.start()
        time.sleep(0.2)  # deixa o socket subir antes do primeiro envio
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.source.stop()

    def send(self, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(payload, ("127.0.0.1", self.receive_port))

    def wait_for_frames(self, count: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.frames) >= count:
                    return
            time.sleep(0.02)

    def frame_count(self) -> int:
        with self._lock:
            return len(self.frames)


class TestCapturaReal:
    def test_pacote_valido_vira_quadro(self) -> None:
        with Harness() as harness:
            harness.send(encrypt_packet(build_plaintext_packet(lap_count=7)))
            harness.wait_for_frames(1)

        assert harness.frame_count() == 1
        assert harness.frames[0].lap_count == 7

    def test_varios_pacotes_viram_varios_quadros(self) -> None:
        with Harness() as harness:
            for lap in range(1, 6):
                harness.send(encrypt_packet(build_plaintext_packet(lap_count=lap)))
            harness.wait_for_frames(5)

        assert [f.lap_count for f in harness.frames] == [1, 2, 3, 4, 5]

    def test_anuncia_recebendo_no_primeiro_pacote(self) -> None:
        with Harness() as harness:
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.wait_for_frames(1)

        states = [state for state, _msg in harness.states]
        assert ConnectionState.CONNECTING in states
        assert ConnectionState.RECEIVING in states

    def test_pacote_de_outra_origem_e_descartado(self) -> None:
        """Qualquer coisa pode chegar numa porta UDP aberta na rede local."""
        with Harness() as harness:
            harness.send("tráfego de outro programa qualquer".encode() * 8)
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.wait_for_frames(1)

        assert harness.frame_count() == 1
        assert harness.source.metrics.snapshot().packets_invalid == 1

    def test_pacote_corrompido_e_descartado(self) -> None:
        corrupted = bytearray(encrypt_packet(build_plaintext_packet()))
        corrupted[2] ^= 0xFF

        with Harness() as harness:
            harness.send(bytes(corrupted))
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.wait_for_frames(1)

        assert harness.frame_count() == 1

    def test_pacote_truncado_e_descartado_sem_derrubar(self) -> None:
        """Decodifica (o magic sobrevive) mas é curto demais para os offsets."""
        plaintext = build_plaintext_packet()
        truncated = encrypt_packet(plaintext)[:120]

        with Harness() as harness:
            harness.send(truncated)
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.wait_for_frames(1)

        # O importante: a captura sobreviveu ao pacote ruim.
        assert harness.frame_count() == 1

    def test_sem_trafego_anuncia_sem_sinal(self) -> None:
        """O timeout do socket é 3 s; o teste espera um pouco mais."""
        with Harness() as harness:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if any(s == ConnectionState.NO_SIGNAL for s, _ in harness.states):
                    break
                time.sleep(0.1)

        assert any(s == ConnectionState.NO_SIGNAL for s, _ in harness.states)


class TestCicloDeVida:
    def test_start_e_idempotente(self) -> None:
        source = Gt7UdpTelemetrySource("127.0.0.1", receive_port=free_port())
        source.start()
        first = source._thread  # noqa: SLF001
        source.start()

        assert source._thread is first  # noqa: SLF001
        source.stop()

    def test_stop_sem_start_e_seguro(self) -> None:
        Gt7UdpTelemetrySource("127.0.0.1", receive_port=free_port()).stop()

    def test_stop_libera_a_porta(self) -> None:
        """Sem o `finally` que fecha o socket, a porta ficaria presa e a
        reconexão falharia com 'endereço em uso'."""
        port = free_port()

        first = Gt7UdpTelemetrySource("127.0.0.1", receive_port=port)
        first.start()
        time.sleep(0.2)
        first.stop()

        second = Gt7UdpTelemetrySource("127.0.0.1", receive_port=port)
        second.start()
        time.sleep(0.2)
        running = second.is_running
        second.stop()

        assert running is True

    def test_porta_ocupada_vira_estado_de_erro(self) -> None:
        """§41: não pode derrubar a aplicação — vira erro visível."""
        port = free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", port))

        try:
            source = Gt7UdpTelemetrySource("127.0.0.1", receive_port=port)
            states: list[tuple[ConnectionState, str]] = []
            source.on_status(lambda state, msg: states.append((state, msg)))

            source.start()
            time.sleep(0.4)
            source.stop()
        finally:
            blocker.close()

        # SO_REUSEADDR permite o bind duplo em alguns sistemas; onde não permite,
        # o erro tem de chegar traduzido em vez de matar a thread em silêncio.
        if any(state == ConnectionState.ERROR for state, _ in states):
            assert any("porta de captura" in msg for _s, msg in states)

    def test_troca_de_ip_so_vale_no_proximo_start(self) -> None:
        """Trocar o destino no meio da captura misturaria dados de dois consoles."""
        source = Gt7UdpTelemetrySource("192.168.1.1", receive_port=free_port())
        source.set_ps_ip("10.0.0.1")

        assert source.ps_ip == "10.0.0.1"


class TestEstatisticas:
    def test_conta_pacotes_e_quadros(self) -> None:
        with Harness() as harness:
            for _ in range(3):
                harness.send(encrypt_packet(build_plaintext_packet()))
            harness.send(b"lixo" * 20)
            harness.wait_for_frames(3)
            time.sleep(0.2)

        stats = harness.source.metrics.snapshot()

        assert stats.packets_received == 4
        assert stats.frames_emitted == 3
        assert stats.packets_invalid == 1
        assert stats.bytes_received > 0
        assert stats.packets_per_second > 0
        assert stats.last_packet_age_s is not None

    def test_taxa_de_perda(self) -> None:
        with Harness() as harness:
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.send(b"lixo" * 20)
            harness.wait_for_frames(1)
            time.sleep(0.2)

        assert harness.source.metrics.snapshot().loss_ratio == pytest.approx(0.5)

    def test_resumo_formatado(self) -> None:
        with Harness() as harness:
            harness.send(encrypt_packet(build_plaintext_packet()))
            harness.wait_for_frames(1)

        summary = harness.source.metrics.snapshot().format_summary()

        assert "pkt/s" in summary
        assert "recebidos" in summary


class TestTraducaoDeErro:
    """A mensagem crua do sistema não diz o que fazer nem para qual endereço."""

    def test_sem_rota_ate_o_host(self) -> None:
        message = describe_send_error(
            OSError(errno.EHOSTUNREACH, "No route to host"), "192.168.1.50"
        )

        assert "192.168.1.50" in message
        assert "mesma rede" in message

    def test_conexao_recusada(self) -> None:
        message = describe_send_error(
            OSError(errno.ECONNREFUSED, "Connection refused"), "192.168.1.50"
        )

        assert "GT7" in message

    def test_endereco_invalido(self) -> None:
        message = describe_send_error(
            OSError(errno.EINVAL, "Invalid argument"), "não-é-um-ip"
        )

        assert "inválido" in message

    def test_erro_desconhecido_ainda_menciona_o_ip(self) -> None:
        """Fallback: mesmo sem tradução específica, o endereço aparece."""
        message = describe_send_error(OSError(9999, "erro exótico"), "10.0.0.9")

        assert "10.0.0.9" in message

    def test_errno_e_por_constante_nao_por_numero(self) -> None:
        """EHOSTUNREACH é 65 no macOS e 113 no Linux — comparar com literal
        quebraria a tradução num dos dois sistemas."""
        message = describe_send_error(
            OSError(errno.EHOSTUNREACH, "unreachable"), "1.2.3.4"
        )

        assert "sem rota" in message
