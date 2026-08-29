"""
Alívio de acelerador: o que conta, e o que o carro fez sozinho.

Dois defeitos silenciosos moravam aqui, e os dois inflavam o mesmo número —
"alívios por volta" — que vai para a tela, para o perfil do piloto e para o
prompt do engenheiro, instruído a nunca inventar grandeza e que portanto
repetia a inflação fielmente.

1. **Autoblip contado como retomada.** O GT7 acelera sozinho na redução de
   marcha. Esse pico virava "aqui o piloto voltou a acelerar", e a volta do
   pedal a zero logo depois entrava como alívio.
2. **Uma soltada contando como muitas.** O contador rebaixava o pico a cada
   queda, então uma soltada contínua de 100% a 0% saía como oito alívios.

Os testes abaixo constroem os dois casos com números conferíveis na mão.
"""

from __future__ import annotations

import pytest

from gt7core.analytics.corners import Corner
from gt7core.analytics.throttle import (
    MIN_APPLICATION_MS,
    analyse_throttle,
)
from gt7core.domain.models import TelemetryPoint

#: 50 Hz — 20 ms por amostra, que é a cadência dos outros testes desta suíte.
PASSO_MS = 20
PASSO_M = 1.0


def _pontos(
    pedais: list[tuple[float, float]],
) -> list[TelemetryPoint]:
    """Uma amostra por par (acelerador, freio), a 20 ms e 1 m de distância."""
    return [
        TelemetryPoint(
            elapsed_ms=i * PASSO_MS,
            distance_m=100.0 + i * PASSO_M,
            speed_kmh=100.0,
            rpm=6000.0,
            gear=3,
            throttle=acelerador,
            brake=freio,
            fuel_level=40.0,
            tire_temp_fl=85.0, tire_temp_fr=85.0,
            tire_temp_rl=85.0, tire_temp_rr=85.0,
            position_x=float(i), position_z=0.0,
            g_lateral=0.0, g_longitudinal=0.0,
            suspension_fl=0.1, suspension_fr=0.1,
            suspension_rl=0.1, suspension_rr=0.1,
            # Rodas girando limpo, para nenhuma patinagem entrar na conta.
            tire_slip_fl=27.7, tire_slip_fr=27.7,
            tire_slip_rl=27.7, tire_slip_rr=27.7,
            turbo_boost=1.0, oil_temp=95.0, water_temp=90.0,
        )
        for i, (acelerador, freio) in enumerate(pedais)
    ]


def _curva(pontos: list[TelemetryPoint]) -> Corner:
    """Curva cujo ápice é a primeira amostra e a saída é a última."""
    return Corner(
        index=1,
        entry_distance_m=pontos[0].distance_m,
        apex_distance_m=pontos[0].distance_m,
        exit_distance_m=pontos[-1].distance_m,
        entry_speed_kmh=100.0,
        minimum_speed_kmh=100.0,
        exit_speed_kmh=100.0,
        entry_time_ms=pontos[0].elapsed_ms,
        apex_time_ms=pontos[0].elapsed_ms,
        exit_time_ms=pontos[-1].elapsed_ms,
        radius_m=None,
    )


def _saida(pedais: list[tuple[float, float]]):  # noqa: ANN202
    pontos = _pontos(pedais)
    saidas = analyse_throttle(pontos, [_curva(pontos)])
    return saidas[0] if saidas else None


class TestUmaSoltadaEUmAlivio:
    def test_soltada_continua_conta_uma_vez(self) -> None:
        """O defeito: rebaixar o pico a cada queda multiplicava o alívio.

        Pedal cheio, soltada suave até zero, e volta. Um piloto fez **um**
        movimento; o contador antigo via oito.
        """
        pedais = (
            [(100.0, 0.0)] * 20
            + [(100.0 - i * 4.0, 0.0) for i in range(26)]   # 100 → 0, suave
            + [(100.0, 0.0)] * 20
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 1

    def test_dois_alivios_de_verdade_contam_dois(self) -> None:
        """O contador não pode resolver o defeito virando cego."""
        pedais = (
            [(100.0, 0.0)] * 20
            + [(50.0, 0.0)] * 10     # primeiro alívio
            + [(100.0, 0.0)] * 20    # reaplica
            + [(45.0, 0.0)] * 10     # segundo alívio
            + [(100.0, 0.0)] * 20
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 2

    def test_saida_limpa_nao_tem_alivio(self) -> None:
        pedais = [(20.0 + i * 2.0, 0.0) for i in range(41)]  # 20 → 100, subindo
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 0
        assert saida.is_clean

    def test_ruido_de_mao_nao_conta(self) -> None:
        """Oscilação abaixo do limiar é tremor de gatilho, não decisão."""
        pedais = [(100.0 if i % 2 else 94.0, 0.0) for i in range(40)]
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 0


class TestAutoblip:
    def test_sobreposicao_longa_de_pedais_nao_e_retomada(self) -> None:
        """O caso que **só** a trava de freio pega.

        Acelerador apoiado por 400 ms com o freio ainda em 60% — sobreposição
        de pedais, não pico de troca de marcha. A trava de duração deixa passar,
        porque isto dura mais que `MIN_APPLICATION_MS`; o que diz que ainda não
        é a retomada é o pedido de desaceleração acontecendo junto.

        A retomada da saída é onde o piloto **compromete** com acelerar, e isso
        é quando o freio sai.
        """
        pedais = (
            [(0.0, 80.0)] * 10
            + [(40.0, 60.0)] * 20       # 400 ms de sobreposição
            + [(0.0, 30.0)] * 5
            + [(95.0, 0.0)] * 40        # retomada de verdade
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.delay_from_apex_m == pytest.approx(35.0)
        assert saida.lift_count == 0

    def test_pico_com_freio_apoiado_nao_e_retomada(self) -> None:
        """Redução de marcha em trail braking — o caso comum.

        O carro dá o pico de acelerador para casar a rotação enquanto o piloto
        ainda está freando. Contado como retomada, ele mentia duas vezes: dizia
        que a aceleração começou 20 m antes, e a queda logo depois virava
        alívio.

        Aqui as duas travas concordam, e é de propósito: um pico curto **com**
        freio é o caso mais comum de todos, e vale ter um teste que falha se
        qualquer uma das duas quebrar.
        """
        pedais = (
            [(0.0, 80.0)] * 10          # freando
            + [(45.0, 75.0)] * 8        # autoblip, freio ainda apoiado
            + [(0.0, 60.0)] * 10        # segue freando
            + [(0.0, 0.0)] * 5          # inércia
            + [(90.0, 0.0)] * 40        # retomada de verdade
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 0, "o blip não pode virar alívio"
        # A retomada é a de verdade, 33 amostras depois do ápice.
        assert saida.delay_from_apex_m == pytest.approx(33.0)

    def test_pico_curto_sem_freio_tambem_e_descartado(self) -> None:
        """Redução feita de inércia, já fora do freio.

        Aqui a trava do freio não pega nada — o freio está solto. O que denuncia
        é a duração: 160 ms sobem e voltam, e retomada de verdade dura até a
        próxima frenagem.
        """
        pedais = (
            [(0.0, 0.0)] * 10
            + [(50.0, 0.0)] * 8         # 160 ms de blip
            + [(0.0, 0.0)] * 10
            + [(90.0, 0.0)] * 40        # retomada
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.lift_count == 0
        assert saida.delay_from_apex_m == pytest.approx(28.0)

    def test_um_pico_longo_e_retomada_e_nao_blip(self) -> None:
        """A trava de duração não pode engolir uma retomada curta de verdade.

        Um pedal que fica de pé mais que `MIN_APPLICATION_MS` é decisão do
        piloto, mesmo que ele alivie logo em seguida — e aí o alívio conta.
        """
        amostras_do_limiar = MIN_APPLICATION_MS // PASSO_MS + 2
        pedais = (
            [(0.0, 0.0)] * 5
            + [(60.0, 0.0)] * amostras_do_limiar
            + [(0.0, 0.0)] * 20
        )
        saida = _saida(pedais)
        assert saida is not None
        assert saida.delay_from_apex_m == pytest.approx(5.0)
        assert saida.lift_count == 1

    def test_curva_sem_retomada_nenhuma_nao_produz_saida(self) -> None:
        """Só blips e freio: não houve retomada a medir."""
        pedais = [(0.0, 70.0)] * 10 + [(40.0, 65.0)] * 6 + [(0.0, 70.0)] * 10
        assert _saida(pedais) is None
