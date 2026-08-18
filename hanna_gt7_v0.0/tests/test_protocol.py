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

from .conftest import (
    LOCKED_WHEEL_RATIO,
    build_plaintext_packet,
    encrypt_packet,
)


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
        # 0x70 é o tick do jogo, 0x78 o melhor tempo. A versão anterior lia
        # estes dois offsets como `best_lap` e `current_lap` — e o conftest
        # escrevia nos mesmos lugares, então leitor e escritor concordavam e o
        # teste passava com os dois deslocados. Só a captura real revelou.
        assert frame.packet_id == 42_000
        assert frame.last_lap_ms == 101_500
        assert frame.best_lap_ms == 100_250
        assert frame.day_progression_ms == 36_000_000
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


class TestDistanciaComPacoteReal:
    """A regressão que custou uma sessão inteira de pista.

    Contra um PS5 de verdade, toda volta era gravada com `distance_m=0.0`. O
    tempo de volta saía certo, os pontos eram salvos, e nada no log parecia
    errado — mas curvas, zonas de frenagem e atribuição de perda são todas
    indexadas por distância, então a análise inteira nascia morta.

    A causa: 0x78 é o **melhor tempo**, não o tempo corrente. Um valor constante
    dentro da volta, e a distância é integrada sobre `Δt`. Δt de uma constante é
    zero.

    Este teste percorre o pipeline pelo caminho real — bytes → `from_bytes` →
    motor — porque foi exatamente a ausência desse caminho que deixou o defeito
    passar: os testes de motor construíam quadros à mão, e os de protocolo
    conferiam campos isolados. Nenhum ligava os dois.
    """

    def test_o_tick_faz_a_distancia_andar(self) -> None:
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import TelemetryEngine
        from tests.conftest import build_plaintext_packet

        engine = TelemetryEngine(EventBus(), sample_rate_hz=60)

        # 1 s de captura a 60 Hz, 180 km/h constantes = 50 m.
        for tick in range(61):
            packet = build_plaintext_packet(speed_ms=50.0, packet_id=tick)
            frame = TelemetryFrame.from_bytes(bytes(packet))
            assert frame is not None
            engine.on_frame(frame)

        assert engine.current_distance_m == pytest.approx(50.0, rel=1e-6)

    def test_melhor_tempo_constante_nao_e_mais_confundido_com_relogio(self) -> None:
        """O coração do defeito, isolado.

        Com o tick parado e só o melhor tempo variando, a distância **tem** de
        continuar zerada: é a prova de que o motor não voltou a ler o campo
        errado. Se algum dia alguém trocar `packet_id` por `best_lap_ms` de
        novo, este teste passa a ver distância onde não deveria haver nenhuma.
        """
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import TelemetryEngine
        from tests.conftest import build_plaintext_packet

        engine = TelemetryEngine(EventBus(), sample_rate_hz=60)

        for best in (90_000, 91_000, 92_000, 93_000):
            packet = build_plaintext_packet(
                speed_ms=50.0, packet_id=7, best_lap_ms=best
            )
            frame = TelemetryFrame.from_bytes(bytes(packet))
            assert frame is not None
            engine.on_frame(frame)

        assert engine.current_distance_m == 0.0


class TestRodasESuspensao:
    """Os campos que ninguém verificava — e que estavam nos offsets errados.

    Contra um PS5 real as quatro rodas marcavam **0,000 a volta inteira**: o
    escorregamento era lido de 0xE4, que cai no bloco não usado do pacote, e a
    suspensão de 0x98, que cai dentro do vetor do plano da pista. Nenhum teste
    olhava para eles, então a suíte inteira passava com dois canais mortos.

    A lição é a de sempre neste arquivo: campo decodificado sem asserção é campo
    não decodificado.
    """

    def test_velocidade_de_superficie_sai_de_rotacao_vezes_raio(self) -> None:
        """Não existe campo "escorregamento" no pacote — existem rotação e raio.

        A velocidade da superfície é |ω| × raio, que é fisicamente definida.
        Antes disto o módulo de análise inferia a convenção do canal por
        magnitude, porque ninguém sabia o que aquele número era.
        """
        frame = TelemetryFrame.from_bytes(build_plaintext_packet(speed_ms=55.0))

        # Três rodas limpas: superfície na velocidade do carro.
        for roda in ("tire_slip_fl", "tire_slip_fr", "tire_slip_rl"):
            assert getattr(frame, roda) == pytest.approx(55.0, rel=1e-3)

        # A quarta gira a 80% — travando sob freio.
        assert frame.tire_slip_rr == pytest.approx(55.0 * LOCKED_WHEEL_RATIO, rel=1e-3)

    def test_a_razao_de_escorregamento_fecha_com_a_velocidade(self) -> None:
        """A leitura que a tela mostra: 100% é a roda rodando limpa.

        Este é o número que aparecia como 0% na volta inteira.
        """
        frame = TelemetryFrame.from_bytes(build_plaintext_packet(speed_ms=55.0))

        assert frame.tire_slip_fl / 55.0 == pytest.approx(1.0, rel=1e-3)
        assert frame.tire_slip_rr / 55.0 == pytest.approx(LOCKED_WHEEL_RATIO, rel=1e-3)

    def test_suspensao_sai_do_offset_certo(self) -> None:
        frame = TelemetryFrame.from_bytes(build_plaintext_packet())

        assert frame.suspension_fl == pytest.approx(0.11, abs=1e-6)
        assert frame.suspension_fr == pytest.approx(0.12, abs=1e-6)
        assert frame.suspension_rl == pytest.approx(0.13, abs=1e-6)
        assert frame.suspension_rr == pytest.approx(0.14, abs=1e-6)

    def test_nenhuma_roda_zera_num_pacote_valido(self) -> None:
        """O sintoma exato que apareceu contra o console.

        Um canal de telemetria não é identicamente zero. Se este teste cair, o
        offset voltou a apontar para o bloco não usado do pacote.
        """
        frame = TelemetryFrame.from_bytes(build_plaintext_packet())

        for roda in ("fl", "fr", "rl", "rr"):
            assert getattr(frame, f"tire_slip_{roda}") > 0.0
