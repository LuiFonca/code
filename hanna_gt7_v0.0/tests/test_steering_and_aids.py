"""
Guinada derivada do traçado, e as faixas de atuação dos auxílios.

Os dois módulos existem porque o pacote do GT7 **não** traz o que se queria: não
há ângulo de volante, e o estado dos auxílios nunca chegava à volta gravada. Um
deles calcula o que dá para saber; o outro persiste o que já era sabido e é
explícito sobre o que não é (o bit do ABS).

O que se verifica aqui é sobretudo **sinal e ausência**: um sinal trocado põe a
curva para o lado errado sem nada na tela denunciar — foi o que acabou de
acontecer no diagrama G-G —, e um `None` tratado como zero faz uma volta antiga
alegar que nenhum auxílio atuou quando ninguém mediu.
"""

from __future__ import annotations

import math

import pytest

from gt7core.analytics.aids import (
    AIDS,
    MERGE_GAP_MS,
    aid_spans,
    unknown_bits,
    was_recorded,
)
from gt7core.analytics.steering import (
    peak_yaw_rate,
    steer_angle_series,
    yaw_rate_series,
)
from gt7core.domain.models import TelemetryPoint
from gt7core.telemetry.protocol import (
    FLAG_ASM_ACTIVE,
    FLAG_CAR_ON_TRACK,
    FLAG_TCS_ACTIVE,
)


def make_point(
    *,
    elapsed_ms: int,
    distance_m: float,
    speed_kmh: float = 150.0,
    position_x: float = 0.0,
    position_z: float = 0.0,
    flags: int | None = None,
) -> TelemetryPoint:
    return TelemetryPoint(
        elapsed_ms=elapsed_ms,
        distance_m=distance_m,
        speed_kmh=speed_kmh,
        rpm=6000.0,
        gear=4,
        throttle=80.0,
        brake=0.0,
        fuel_level=50.0,
        tire_temp_fl=80.0, tire_temp_fr=80.0,
        tire_temp_rl=80.0, tire_temp_rr=80.0,
        position_x=position_x,
        position_z=position_z,
        g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.1, suspension_fr=0.1,
        suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=1.0, tire_slip_fr=1.0,
        tire_slip_rl=1.0, tire_slip_rr=1.0,
        turbo_boost=1.0, oil_temp=100.0, water_temp=90.0,
        flags=flags,
    )


def circular_lap(
    *, radius_m: float, speed_ms: float, samples: int = 120, clockwise: bool = False
) -> list[TelemetryPoint]:
    """Carro num círculo perfeito — guinada conhecida: `v / R` rad/s."""
    omega = speed_ms / radius_m
    dt = 1.0 / 60.0
    sign = -1.0 if clockwise else 1.0

    points = []
    for i in range(samples):
        t = i * dt
        angle = sign * omega * t
        points.append(
            make_point(
                elapsed_ms=int(t * 1000),
                distance_m=speed_ms * t,
                speed_kmh=speed_ms * 3.6,
                position_x=radius_m * math.cos(angle),
                position_z=radius_m * math.sin(angle),
            )
        )
    return points


class TestGuinada:
    def test_circulo_conhecido_da_a_guinada_certa(self) -> None:
        """Num círculo, guinada = v/R. É o único caso com resposta fechada.

        Sem uma referência analítica, um teste de guinada só compara o código
        consigo mesmo: qualquer erro de escala ou de janela passaria, porque o
        valor "esperado" teria saído da mesma função.
        """
        pontos = circular_lap(radius_m=100.0, speed_ms=30.0)
        esperado = math.degrees(30.0 / 100.0)  # ≈ 17,19 °/s

        serie = yaw_rate_series(pontos)

        assert serie, "o círculo tem amostras de sobra para a janela"
        medido = sum(v for _, v in serie) / len(serie)
        assert medido == pytest.approx(esperado, rel=0.02)

    def test_o_sinal_acompanha_o_da_forca_lateral(self) -> None:
        """Guinada e `g_lateral` têm de concordar sobre para que lado é a curva.

        Não é coincidência a verificar, é identidade: derivando
        `θ = atan2(vz, vx)` sai `dθ/dt = (vx·az − vz·ax)/|v|²`, o mesmo
        numerador de `g_lateral`. O teste prende os dois juntos — se algum dia
        alguém inverter o vetor `right` no motor, este teste cai e mostra que os
        dois canais passaram a discordar.
        """
        pontos = circular_lap(radius_m=100.0, speed_ms=30.0)
        serie = yaw_rate_series(pontos)
        meio = serie[len(serie) // 2][1]

        # `g_lateral` do motor para o mesmo movimento: a projeção da aceleração
        # centrípeta no vetor `right = (-fwd_z, fwd_x)`.
        i = len(pontos) // 2
        anterior, atual, seguinte = pontos[i - 1], pontos[i], pontos[i + 1]
        dt = (seguinte.elapsed_ms - anterior.elapsed_ms) / 1000.0
        vx = (seguinte.position_x - anterior.position_x) / dt
        vz = (seguinte.position_z - anterior.position_z) / dt
        dt2 = (seguinte.elapsed_ms - atual.elapsed_ms) / 1000.0
        ax = ((seguinte.position_x - atual.position_x) / dt2 - vx) / dt2
        az = ((seguinte.position_z - atual.position_z) / dt2 - vz) / dt2
        g_lateral_bruto = vx * az - vz * ax

        assert math.copysign(1.0, meio) == math.copysign(1.0, g_lateral_bruto)

    def test_os_dois_sentidos_saem_com_sinais_opostos(self) -> None:
        horario = yaw_rate_series(circular_lap(radius_m=80.0, speed_ms=25.0, clockwise=True))
        anti = yaw_rate_series(circular_lap(radius_m=80.0, speed_ms=25.0))

        assert horario and anti
        assert math.copysign(1.0, horario[len(horario) // 2][1]) != math.copysign(
            1.0, anti[len(anti) // 2][1]
        )

    def test_reta_nao_inventa_guinada(self) -> None:
        pontos = [
            make_point(elapsed_ms=i * 16, distance_m=i * 0.8, position_x=i * 0.8)
            for i in range(60)
        ]
        assert peak_yaw_rate(yaw_rate_series(pontos)) == pytest.approx(0.0, abs=0.5)

    def test_carro_parado_nao_gera_pico(self) -> None:
        """Parado, `atan2` de dois zeros é ruído — e viraria picos absurdos.

        A amostra é **omitida**, não zerada: zero afirmaria "seguiu reto", que é
        informação que não foi medida.
        """
        pontos = [
            make_point(elapsed_ms=i * 16, distance_m=0.0, speed_kmh=0.0)
            for i in range(60)
        ]
        assert yaw_rate_series(pontos) == []

    def test_cruzar_a_descontinuidade_nao_vira_pico(self) -> None:
        """O círculo passa por ±π uma vez por volta.

        Sem trazer a diferença de ângulos para (−π, π], aquele quadro sozinho
        vira um salto de 2π — um pico de ~20.000 °/s que reescala o gráfico
        inteiro e achata a volta de verdade.
        """
        pontos = circular_lap(radius_m=100.0, speed_ms=30.0, samples=400)
        serie = yaw_rate_series(pontos)

        assert peak_yaw_rate(serie) < 3 * math.degrees(30.0 / 100.0)

    def test_volta_curta_demais_devolve_vazio(self) -> None:
        assert yaw_rate_series([make_point(elapsed_ms=0, distance_m=0.0)]) == []


class TestAuxilios:
    def test_acha_o_trecho_de_atuacao(self) -> None:
        pontos = [
            make_point(
                elapsed_ms=i * 100,
                distance_m=i * 10.0,
                flags=FLAG_CAR_ON_TRACK | (FLAG_TCS_ACTIVE if 3 <= i <= 6 else 0),
            )
            for i in range(12)
        ]

        trechos = aid_spans(pontos, "TCS")

        assert len(trechos) == 1
        assert trechos[0].start_distance_m == 30.0
        assert trechos[0].end_distance_m == 60.0
        assert trechos[0].duration_ms == 300

    def test_cada_auxilio_le_o_seu_bit(self) -> None:
        pontos = [
            make_point(elapsed_ms=i * 100, distance_m=i * 10.0, flags=FLAG_ASM_ACTIVE)
            for i in range(6)
        ]

        assert aid_spans(pontos, "ASM")
        assert aid_spans(pontos, "TCS") == []

    def test_modulacao_rapida_vira_um_episodio(self) -> None:
        """O TCS cicla em dezenas de milissegundos.

        Desenhado cru, um episódio de 1 s vira um pente de fatias de dois pixels
        que não se lê como nada. Unir os intervalos curtos mostra o episódio sem
        apagar que houve modulação.
        """
        pontos = [
            make_point(
                elapsed_ms=i * 16,
                distance_m=i * 1.0,
                flags=FLAG_TCS_ACTIVE if i % 2 == 0 else 0,
            )
            for i in range(40)
        ]

        trechos = aid_spans(pontos, "TCS")

        assert len(trechos) == 1, "trinta fatias de 16 ms são um episódio, não trinta"

    def test_intervalo_longo_continua_sendo_dois_episodios(self) -> None:
        """A união não pode engolir a diferença entre uma curva e a seguinte."""
        pontos = [
            make_point(
                elapsed_ms=i * 100,
                distance_m=i * 10.0,
                flags=FLAG_TCS_ACTIVE if i in (1, 2, 20, 21) else 0,
            )
            for i in range(30)
        ]

        assert MERGE_GAP_MS < (20 - 2) * 100
        assert len(aid_spans(pontos, "TCS")) == 2

    def test_atuacao_ate_o_fim_da_volta_e_fechada(self) -> None:
        """Sem fechar o trecho aberto, a última atuação da volta some."""
        pontos = [
            make_point(
                elapsed_ms=i * 100,
                distance_m=i * 10.0,
                flags=FLAG_TCS_ACTIVE if i >= 7 else 0,
            )
            for i in range(10)
        ]

        trechos = aid_spans(pontos, "TCS")

        assert len(trechos) == 1
        assert trechos[0].end_distance_m == 90.0

    def test_volta_antiga_nao_alega_pilotagem_limpa(self) -> None:
        """`flags` nulo é "não foi medido", e não "nenhum auxílio atuou".

        Sem esta distinção, toda volta gravada antes da versão 7 do banco
        passaria a exibir uma faixa vazia — uma afirmação sobre o TCS que
        ninguém observou.
        """
        antigos = [make_point(elapsed_ms=i * 100, distance_m=i * 10.0) for i in range(10)]

        assert was_recorded(antigos) is False
        assert aid_spans(antigos, "TCS") == []

        novos = [
            make_point(elapsed_ms=i * 100, distance_m=i * 10.0, flags=FLAG_CAR_ON_TRACK)
            for i in range(10)
        ]
        assert was_recorded(novos) is True

    def test_auxilio_desconhecido_nao_estoura(self) -> None:
        pontos = [make_point(elapsed_ms=0, distance_m=0.0, flags=FLAG_TCS_ACTIVE)]
        assert aid_spans(pontos, "ABS") == []
        assert "ABS" not in AIDS, "o bit do ABS não está identificado — não fingir que está"


class TestCacaAoABS:
    def test_reporta_bits_sem_nome(self) -> None:
        """O instrumento para achar o ABS sem chutar offset.

        Freia forte com ABS ligado, freia com ele desligado, compara. O bit que
        aparece só na primeira é o candidato — e vira fato depois de repetir.
        """
        candidato = 1 << 13
        pontos = [
            make_point(
                elapsed_ms=i * 100,
                distance_m=i * 10.0,
                flags=FLAG_CAR_ON_TRACK | (candidato if i > 4 else 0),
            )
            for i in range(10)
        ]

        assert unknown_bits(pontos) == candidato

    def test_bits_conhecidos_nao_aparecem_como_novidade(self) -> None:
        pontos = [
            make_point(
                elapsed_ms=i * 100,
                distance_m=i * 10.0,
                flags=FLAG_CAR_ON_TRACK | FLAG_TCS_ACTIVE | FLAG_ASM_ACTIVE,
            )
            for i in range(10)
        ]

        assert unknown_bits(pontos) == 0

    def test_volta_sem_flags_nao_reporta_nada(self) -> None:
        pontos = [make_point(elapsed_ms=0, distance_m=0.0)]
        assert unknown_bits(pontos) == 0


class TestEstercoEstimado:
    """O canal de volante que o GT7 não transmite, derivado da geometria.

    O círculo é o único caso com resposta fechada: num raio `R`, a geometria de
    Ackermann dá `δ = atan(L/R)` **independente da velocidade**. Um teste que
    passasse só numa velocidade estaria medindo a guinada disfarçada de esterço.
    """

    def test_circulo_da_o_angulo_de_ackermann(self) -> None:
        pontos = circular_lap(radius_m=50.0, speed_ms=25.0)

        serie = steer_angle_series(pontos, wheelbase_m=2.6)
        esperado = math.degrees(math.atan(2.6 / 50.0))

        assert serie
        assert all(abs(v - esperado) < 0.05 for _, v in serie)

    def test_nao_depende_da_velocidade(self) -> None:
        """Mesmo raio, o dobro da velocidade, mesmo esterço.

        É o que separa esterço de guinada: no mesmo círculo mais rápido o carro
        gira o dobro por segundo, mas as rodas apontam para o mesmo lugar. Se
        este teste reprovar, o gráfico está desenhando guinada com outro rótulo.
        """
        devagar = steer_angle_series(circular_lap(radius_m=50.0, speed_ms=15.0))
        rapido = steer_angle_series(circular_lap(radius_m=50.0, speed_ms=30.0))

        media_devagar = sum(v for _, v in devagar) / len(devagar)
        media_rapido = sum(v for _, v in rapido) / len(rapido)

        assert abs(media_devagar - media_rapido) < 0.05

    def test_entre_eixos_escala_sem_deformar(self) -> None:
        """O único palpite da conta muda a escala, não a forma.

        É o que autoriza mostrar o gráfico apesar de o entre-eixos ser suposto:
        onde o esterço entra e quanto dura continua verdadeiro.
        """
        curto = steer_angle_series(circular_lap(radius_m=80.0, speed_ms=30.0),
                                   wheelbase_m=2.3)
        longo = steer_angle_series(circular_lap(radius_m=80.0, speed_ms=30.0),
                                   wheelbase_m=2.9)

        assert len(curto) == len(longo)
        assert all(
            abs(baixo) < abs(alto)
            for (_, baixo), (_, alto) in zip(curto, longo, strict=True)
        )

    def test_sinal_acompanha_a_guinada(self) -> None:
        """Positivo é direita nos dois canais, ou um deles está espelhado."""
        horario = circular_lap(radius_m=60.0, speed_ms=25.0, clockwise=True)

        guinada = yaw_rate_series(horario)
        esterco = steer_angle_series(horario)

        assert all(w * d > 0 for (_, w), (_, d) in zip(guinada, esterco, strict=True))

    def test_reta_nao_esterca(self) -> None:
        pontos = [
            make_point(elapsed_ms=i * 16, distance_m=i * 1.0, position_x=i * 1.0)
            for i in range(60)
        ]

        assert all(abs(v) < 0.2 for _, v in steer_angle_series(pontos))
