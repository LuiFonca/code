"""
O indicador de conexão volta sozinho quando o sinal volta.

Este arquivo tranca um defeito que só aparecia com um console de verdade e três
segundos de silêncio: `RECEIVING` era emitido **uma vez**, na guarda de primeiro
pacote, e o retorno do silêncio limpava a bandeira interna sem avisar ninguém. O
botão ficava em SEM SINAL para sempre com a telemetria chegando atrás.

Três segundos calados é coisa de todo dia no GT7: entrar num menu, uma tela de
carregamento, um soluço de rede. O teste reproduz exatamente isso, com um PS5
falso que fala o protocolo de verdade — pacote de 296 bytes cifrado em Salsa20,
com o magic number no lugar — porque um dublê que devolvesse quadros prontos
pularia justamente o caminho onde o defeito morava.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading
import time

from gt7core.telemetry.protocol import GT7_KEY, MAGIC_NUMBER
from gt7core.telemetry.sources.base import ConnectionState
from gt7core.telemetry.sources.udp import (
    SOCKET_TIMEOUT_S,
    Gt7UdpTelemetrySource,
)

#: Portas fora das padrão, para o teste não disputar com um PS5 real na rede
#: nem com outra cópia do programa aberta na mesma máquina.
PORTA_ENVIO = 33839
PORTA_RECEBIMENTO = 33840

#: Silêncio encenado. Precisa passar de `SOCKET_TIMEOUT_S` com folga, senão o
#: teste vira uma corrida contra o relógio do laço de captura.
SILENCIO_S = SOCKET_TIMEOUT_S + 1.5

#: Quanto esperar por um estado antes de desistir.
PACIENCIA_S = 5.0


def _pacote(tick: int) -> bytes:
    """Um quadro GT7 válido, cifrado como o console cifra."""
    from Crypto.Cipher import Salsa20

    bruto = bytearray(296)
    struct.pack_into("<i", bruto, 0x00, MAGIC_NUMBER)
    struct.pack_into("<i", bruto, 0x70, tick)
    struct.pack_into("<H", bruto, 0x8E, 1)        # carro na pista
    struct.pack_into("<f", bruto, 0x4C, 100.0)    # velocidade
    iv1 = 0x11223344
    iv = (iv1 ^ 0xDEADBEAF).to_bytes(4, "little") + iv1.to_bytes(4, "little")
    cifrado = bytearray(
        Salsa20.new(key=GT7_KEY[:32], nonce=bytes(iv)).encrypt(bytes(bruto))
    )
    cifrado[0x40:0x44] = iv1.to_bytes(4, "little")
    return bytes(cifrado)


class _PS5Falso:
    """Responde ao heartbeat com telemetria, e sabe ficar calado sob comando."""

    def __init__(self) -> None:
        self._parar = threading.Event()
        self.calado = threading.Event()
        self._thread = threading.Thread(target=self._servir, daemon=True)

    def __enter__(self) -> _PS5Falso:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._parar.set()
        self._thread.join(timeout=2.0)

    def _servir(self) -> None:
        ouve = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ouve.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ouve.bind(("127.0.0.1", PORTA_ENVIO))
        ouve.settimeout(0.1)
        envia = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tick = 0
        try:
            while not self._parar.is_set():
                with contextlib.suppress(TimeoutError, OSError):
                    ouve.recvfrom(64)
                if self.calado.is_set():
                    time.sleep(0.05)
                    continue
                for _ in range(6):
                    tick += 1
                    envia.sendto(_pacote(tick), ("127.0.0.1", PORTA_RECEBIMENTO))
                    time.sleep(0.016)
        finally:
            ouve.close()
            envia.close()


def _esperar(estados: list[ConnectionState], alvo: ConnectionState) -> bool:
    """Espera o estado chegar, ou desiste. Devolve se chegou."""
    prazo = time.monotonic() + PACIENCIA_S
    while time.monotonic() < prazo:
        if estados and estados[-1] == alvo:
            return True
        time.sleep(0.05)
    return False


def test_o_indicador_volta_de_sem_sinal_sozinho() -> None:
    """Fluindo → calado → fluindo. O indicador tem que acompanhar os três."""
    estados: list[ConnectionState] = []

    with _PS5Falso() as ps5:
        fonte = Gt7UdpTelemetrySource(
            "127.0.0.1", send_port=PORTA_ENVIO, receive_port=PORTA_RECEBIMENTO
        )
        fonte.on_status(lambda estado, _mensagem=None: estados.append(estado))
        fonte.on_frame(lambda _quadro: None)
        fonte.start()
        try:
            assert _esperar(estados, ConnectionState.RECEIVING), (
                f"não chegou a receber; estados={estados}"
            )

            ps5.calado.set()
            assert _esperar(estados, ConnectionState.NO_SIGNAL), (
                f"silêncio não foi reportado; estados={estados}"
            )
            time.sleep(SILENCIO_S - SOCKET_TIMEOUT_S)

            ps5.calado.clear()
            recuperou = _esperar(estados, ConnectionState.RECEIVING)
        finally:
            fonte.stop()

    assert recuperou, (
        "o indicador ficou preso em SEM SINAL com a telemetria voltando; "
        f"estados={[e.value for e in estados]}"
    )
    # A sequência inteira, para a intenção ficar legível em cima do assert.
    valores = [e.value for e in estados]
    assert valores[:4] == ["connecting", "receiving", "no_signal", "receiving"]


def test_nao_repete_receiving_a_cada_pacote() -> None:
    """Anunciar o mesmo estado 60 vezes por segundo é ruído, não informação.

    A correção do defeito acima é emitir `RECEIVING` ao sair do silêncio — e o
    jeito errado de fazer isso seria emitir a cada pacote, o que a interface
    teria de filtrar de novo.
    """
    estados: list[ConnectionState] = []

    with _PS5Falso():
        fonte = Gt7UdpTelemetrySource(
            "127.0.0.1", send_port=PORTA_ENVIO, receive_port=PORTA_RECEBIMENTO
        )
        fonte.on_status(lambda estado, _mensagem=None: estados.append(estado))
        fonte.on_frame(lambda _quadro: None)
        fonte.start()
        try:
            assert _esperar(estados, ConnectionState.RECEIVING)
            time.sleep(1.5)   # ~90 pacotes
        finally:
            fonte.stop()

    recebendo = [e for e in estados if e == ConnectionState.RECEIVING]
    assert len(recebendo) == 1, (
        f"RECEIVING foi anunciado {len(recebendo)} vezes em 1,5 s de fluxo"
    )
