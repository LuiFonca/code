"""
Testes do decodificador do protocolo GT7.

O README do projeto afirmava que o protocolo fora validado com "pacote sintético
cifrado com Salsa20 real: todos os campos byte-exatos". A verificação existiu,
mas nunca foi commitada como teste executável — era exatamente o P1 da auditoria.
Este arquivo torna aquela validação repetível.

Cobre também os casos extremos que o §38 do briefing pede explicitamente:
pacote corrompido, curto, duplicado e de origem desconhecida.
"""

from __future__ import annotations

import struct

import pytest

from gt7core.telemetry.protocol import (
    FLAG_CAR_ON_TRACK,
    FLAG_PAUSED,
    FLAG_TCS_ACTIVE,
    TelemetryFrame,
    salsa20_decode,
)

from .conftest import build_plaintext_packet, encrypt_packet


class TestDecodificacao:
    def test_pacote_valido_decodifica(self, valid_packet: bytes) -> None:
        assert salsa20_decode(valid_packet) is not None

    def test_campos_sao_byte_exatos(self, valid_packet: bytes) -> None:
        """Cada campo confere com o valor que foi escrito no pacote em claro."""
        frame = TelemetryFrame.from_bytes(salsa20_decode(valid_packet))  # type: ignore[arg-type]

        # Velocidade vem em m/s no fio e é convertida para km/h.
        assert frame.speed_kmh == pytest.approx(55.0 * 3.6)
        assert frame.rpm == pytest.approx(6400.0)
        assert frame.lap_count == 3
        assert frame.current_lap_ms == 42_000
        assert frame.last_lap_ms == 101_500
        assert frame.best_lap_ms == 100_250
        assert frame.car_id == 1234
        assert frame.fuel == pytest.approx(42.5)
        assert frame.position_x == pytest.approx(10.0)
        assert frame.position_z == pytest.approx(30.0)
        assert frame.water_temp == pytest.approx(89.0)
        assert frame.oil_temp == pytest.approx(105.0)

    def test_nibbles_de_marcha_sao_separados(self) -> None:
        """Um byte carrega marcha atual (baixo) e sugerida (alto)."""
        packet = encrypt_packet(build_plaintext_packet(gear=3, suggested_gear=7))
        frame = TelemetryFrame.from_bytes(salsa20_decode(packet))  # type: ignore[arg-type]

        assert frame.gear == 3
        assert frame.suggested_gear == 7

    def test_pedais_normalizados_de_0_255_para_porcentagem(self) -> None:
        packet = encrypt_packet(build_plaintext_packet(throttle_raw=255, brake_raw=128))
        frame = TelemetryFrame.from_bytes(salsa20_decode(packet))  # type: ignore[arg-type]

        assert frame.throttle == pytest.approx(100.0)
        assert frame.brake == pytest.approx(128 / 255 * 100)

    def test_flags_viram_propriedades(self) -> None:
        flags = FLAG_CAR_ON_TRACK | FLAG_PAUSED | FLAG_TCS_ACTIVE
        packet = encrypt_packet(build_plaintext_packet(flags=flags))
        frame = TelemetryFrame.from_bytes(salsa20_decode(packet))  # type: ignore[arg-type]

        assert frame.is_on_track is True
        assert frame.is_paused is True
        assert frame.tcs_active is True
        assert frame.is_loading is False
        assert frame.rev_limiter_active is False


class TestCasosExtremos:
    """§38: pacote corrompido, curto, duplicado, de outra origem."""

    def test_magic_number_errado_e_rejeitado(self) -> None:
        """Sem isso, lixo de outra origem viraria telemetria inventada."""
        packet = encrypt_packet(build_plaintext_packet(magic=0xDEADBEEF))
        assert salsa20_decode(packet) is None

    def test_chave_errada_e_rejeitada(self) -> None:
        """Decifrar com chave errada produz ruído; o magic number pega isso."""
        packet = encrypt_packet(build_plaintext_packet(), key=b"X" * 32)
        assert salsa20_decode(packet) is None

    def test_pacote_corrompido_e_rejeitado(self, valid_packet: bytes) -> None:
        corrupted = bytearray(valid_packet)
        corrupted[2] ^= 0xFF  # corrompe dentro do magic number
        assert salsa20_decode(bytes(corrupted)) is None

    def test_ruido_aleatorio_e_rejeitado(self) -> None:
        assert salsa20_decode(bytes(range(256)) * 2) is None

    def test_pacote_curto_levanta_struct_error(self) -> None:
        """Truncado: o listener trata descartando, mas a exceção precisa existir
        para não virar leitura de lixo silenciosa."""
        plaintext = build_plaintext_packet()[:100]
        with pytest.raises(struct.error):
            TelemetryFrame.from_bytes(bytes(plaintext))

    def test_pacote_duplicado_decodifica_identico(self, valid_packet: bytes) -> None:
        """Decodificação é pura: sem estado entre chamadas."""
        first = TelemetryFrame.from_bytes(salsa20_decode(valid_packet))  # type: ignore[arg-type]
        second = TelemetryFrame.from_bytes(salsa20_decode(valid_packet))  # type: ignore[arg-type]
        assert first == second

    def test_valores_negativos_de_tempo_sobrevivem(self) -> None:
        """O GT7 usa -1 para 'ainda não há volta'. Precisa chegar como -1, não
        como inteiro sem sinal gigante."""
        packet = encrypt_packet(build_plaintext_packet(best_lap_ms=-1, last_lap_ms=-1))
        frame = TelemetryFrame.from_bytes(salsa20_decode(packet))  # type: ignore[arg-type]

        assert frame.best_lap_ms == -1
        assert frame.last_lap_ms == -1
