"""Fixtures compartilhadas."""

from __future__ import annotations

import os
import struct
import sys

import pytest
from Crypto.Cipher import Salsa20

from gt7core.events.bus import EventBus
from gt7core.telemetry.protocol import GT7_KEY, MAGIC_NUMBER

PACKET_SIZE = 296


def _default_to_offscreen() -> None:
    """Sem tela, o Qt aborta o processo — e leva a suíte inteira junto.

    Não é uma falha de teste: é `Fatal Python error: Aborted` dentro do
    `QApplication([])`, que mata o interpretador antes do pytest conseguir
    reportar qualquer coisa. Os testes que rodaram antes somem do relatório, e o
    que aparece na tela é um despejo de pilha do CPython — o suficiente para
    alguém concluir que o projeto está quebrado quando só falta uma variável de
    ambiente.

    Rodar `pytest` sem argumento nenhum é o gesto padrão, então ele é que
    precisa funcionar. Só mexemos onde o problema existe — Linux sem servidor
    gráfico —, e só se ninguém tiver escolhido antes: no Windows e no macOS o Qt
    não usa `DISPLAY`, e forçar `offscreen` lá esconderia a interface de quem
    quisesse vê-la.
    """
    if sys.platform in {"win32", "cygwin", "darwin"}:
        return
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_default_to_offscreen()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def build_plaintext_packet(
    *,
    speed_ms: float = 55.0,
    rpm: float = 6400.0,
    gear: int = 4,
    suggested_gear: int = 5,
    throttle_raw: int = 255,
    brake_raw: int = 0,
    lap_count: int = 3,
    current_lap_ms: int = 42_000,
    last_lap_ms: int = 101_500,
    best_lap_ms: int = 100_250,
    car_id: int = 1234,
    flags: int = 0x0009,
    magic: int = MAGIC_NUMBER,
) -> bytearray:
    """Monta um pacote GT7 **em claro** nos offsets reais do protocolo.

    Existe para que o teste de decodificação valide byte a byte contra valores
    conhecidos, em vez de contra o próprio decodificador. É o oposto de um teste
    tautológico: se um offset for trocado, o valor lido muda e o teste falha.
    """
    packet = bytearray(PACKET_SIZE)

    struct.pack_into("<I", packet, 0x00, magic)
    struct.pack_into("<fff", packet, 0x04, 10.0, 20.0, 30.0)      # posição
    struct.pack_into("<fff", packet, 0x10, 1.5, 2.5, 3.5)         # velocidade vetorial
    struct.pack_into("<f", packet, 0x38, 0.15)                    # altura
    struct.pack_into("<f", packet, 0x3C, rpm)
    struct.pack_into("<I", packet, 0x40, 0x0000_1111)             # IV
    struct.pack_into("<f", packet, 0x44, 42.5)                    # combustível
    struct.pack_into("<f", packet, 0x48, 60.0)                    # capacidade
    struct.pack_into("<f", packet, 0x4C, speed_ms)
    struct.pack_into("<f", packet, 0x50, 1.8)                     # turbo
    struct.pack_into("<f", packet, 0x54, 5.1)                     # pressão de óleo
    struct.pack_into("<f", packet, 0x58, 89.0)                    # água
    struct.pack_into("<f", packet, 0x5C, 105.0)                   # óleo
    struct.pack_into("<ffff", packet, 0x60, 80.0, 82.0, 78.0, 79.0)  # pneus
    struct.pack_into("<i", packet, 0x70, best_lap_ms)
    struct.pack_into("<h", packet, 0x74, lap_count)
    struct.pack_into("<h", packet, 0x76, 10)                      # total de voltas
    struct.pack_into("<i", packet, 0x78, current_lap_ms)
    struct.pack_into("<i", packet, 0x7C, last_lap_ms)
    struct.pack_into("<H", packet, 0x88, 7000)
    struct.pack_into("<H", packet, 0x8A, 7600)
    struct.pack_into("<H", packet, 0x8C, 330)
    struct.pack_into("<H", packet, 0x8E, flags)
    # Um byte carrega duas marchas: nibble baixo = atual, alto = sugerida.
    struct.pack_into("<B", packet, 0x90, (suggested_gear << 4) | gear)
    struct.pack_into("<B", packet, 0x91, throttle_raw)
    struct.pack_into("<B", packet, 0x92, brake_raw)
    struct.pack_into("<ffff", packet, 0x98, 0.11, 0.12, 0.13, 0.14)  # suspensão
    struct.pack_into("<ffff", packet, 0xE4, 1.01, 1.02, 1.03, 1.04)  # slip
    struct.pack_into("<i", packet, 0x124, car_id)

    return packet


def encrypt_packet(plaintext: bytes, key: bytes = GT7_KEY[:32]) -> bytes:
    """Cifra um pacote com o mesmo esquema do GT7 (Salsa20, IV no offset 0x40).

    O detalhe que importa: o receptor lê o IV do pacote **cifrado**, então esses
    4 bytes viajam em claro. Como Salsa20 é cifra de fluxo, encriptá-los junto
    com o resto os transformaria e o receptor derivaria o nonce errado — por
    isso o IV é escrito por cima do texto cifrado, depois de cifrar.
    """
    oiv = bytes(plaintext[0x40:0x44])
    iv1 = int.from_bytes(oiv, byteorder="little")
    iv2 = iv1 ^ 0xDEADBEAF
    nonce = iv2.to_bytes(4, "little") + iv1.to_bytes(4, "little")

    ciphertext = bytearray(Salsa20.new(key=key, nonce=nonce).encrypt(bytes(plaintext)))
    ciphertext[0x40:0x44] = oiv
    return bytes(ciphertext)


@pytest.fixture
def valid_packet() -> bytes:
    return encrypt_packet(build_plaintext_packet())
