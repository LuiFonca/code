"""
Diagnóstico por curva: o apontamento cai na curva certa, e só quando é hábito.

Os dois defeitos que estes testes existem para pegar são silenciosos. Um
apontamento na curva errada parece um conselho legítimo — o piloto vai treinar
a curva 3 por causa de um travamento que aconteceu na 7. E um apontamento
disparado por um evento isolado manda corrigir o que não está errado, que é pior
que não apontar nada: gasta a confiança de quem lê.
"""

from __future__ import annotations

import math

from gt7core.analytics.coaching import (
    MIN_OCCURRENCES,
    diagnose_corners,
)
from gt7core.domain.models import TelemetryPoint

#: Geometria da volta sintética: duas curvas, com ápices bem separados.
APEX_1_M = 300.0
APEX_2_M = 900.0
LAP_LENGTH_M = 1200.0
STEP_M = 2.0


def _velocidade(distancia: float) -> float:
    """Rápido nas retas, lento nos dois ápices — o suficiente para o detector
    de curvas achar duas curvas e não achar uma terceira."""
    v = 200.0
    for apex in (APEX_1_M, APEX_2_M):
        v -= 130.0 * math.exp(-(((distancia - apex) / 70.0) ** 2))
    return max(60.0, v)


def _volta(
    *,
    travar_em: float | None = None,
    patinar_em: float | None = None,
    freada_em: float | None = None,
) -> list[TelemetryPoint]:
    """Uma volta sintética com o defeito pedido, na distância pedida.

    O escorregamento é emitido em **m/s de superfície do pneu**, que é a
    convenção do pacote do GT7 — a mesma que `infer_slip_convention` reconhece.
    Emitir razão adimensional aqui faria o teste passar por um caminho que o
    programa nunca percorre com dados reais.
    """
    pontos: list[TelemetryPoint] = []
    tempo_ms = 0.0
    for i in range(int(LAP_LENGTH_M / STEP_M)):
        d = i * STEP_M
        v_kmh = _velocidade(d)
        v_ms = v_kmh / 3.6
        tempo_ms += STEP_M / v_ms * 1000.0

        # Pedais coerentes com o perfil: freio antes do ápice, acelerador depois.
        freio = 0.0
        acelerador = 100.0
        for apex in (APEX_1_M, APEX_2_M):
            if apex - 120.0 <= d < apex:
                freio = 95.0 * (1.0 - (apex - d) / 120.0)
                acelerador = 0.0
            elif apex <= d < apex + 60.0:
                acelerador = 40.0 + 60.0 * (d - apex) / 60.0

        if freada_em is not None and abs(d - freada_em) < 60.0:
            freio = max(freio, 90.0)
            acelerador = 0.0

        # Rodas girando limpo: superfície na velocidade do carro.
        dianteira = traseira = v_ms
        if travar_em is not None and abs(d - travar_em) < 12.0:
            dianteira = v_ms * 0.70   # roda travando
        if patinar_em is not None and abs(d - patinar_em) < 12.0:
            traseira = v_ms * 1.30    # roda patinando

        pontos.append(
            TelemetryPoint(
                elapsed_ms=int(tempo_ms), distance_m=d, speed_kmh=v_kmh,
                rpm=6000.0, gear=4, throttle=acelerador, brake=freio,
                fuel_level=40.0,
                tire_temp_fl=85.0, tire_temp_fr=85.0,
                tire_temp_rl=85.0, tire_temp_rr=85.0,
                position_x=d, position_z=0.0,
                g_lateral=0.5, g_longitudinal=-0.5 if freio else 0.3,
                suspension_fl=0.0, suspension_fr=0.0,
                suspension_rl=0.0, suspension_rr=0.0,
                tire_slip_fl=dianteira, tire_slip_fr=dianteira,
                tire_slip_rl=traseira, tire_slip_rr=traseira,
                turbo_boost=1.0, oil_temp=95.0, water_temp=90.0,
            )
        )
    return pontos


def _numeros(reports) -> list[int]:  # noqa: ANN001
    return [r.number for r in reports]


def _tipos(reports, numero: int) -> set[str]:  # noqa: ANN001
    for r in reports:
        if r.number == numero:
            return {i.kind for i in r.issues}
    return set()


class TestOndeOApontamentoCai:
    def test_o_travamento_e_atribuido_a_curva_em_que_aconteceu(self) -> None:
        """O defeito silencioso: apontar a curva errada parece conselho bom."""
        voltas = [_volta(travar_em=APEX_2_M - 40.0) for _ in range(6)]
        reports = diagnose_corners(voltas)

        assert "travamento" in _tipos(reports, 2)
        assert "travamento" not in _tipos(reports, 1)

    def test_patinagem_na_saida_e_travamento_na_entrada_nao_se_confundem(self) -> None:
        """Freio e acelerador exigem conselhos opostos; somá-los apaga os dois."""
        voltas = [
            _volta(travar_em=APEX_1_M - 40.0, patinar_em=APEX_2_M + 40.0)
            for _ in range(6)
        ]
        reports = diagnose_corners(voltas)

        assert "travamento" in _tipos(reports, 1)
        assert "patinagem" not in _tipos(reports, 1)
        assert "patinagem" in _tipos(reports, 2)
        assert "travamento" not in _tipos(reports, 2)


class TestRecorrencia:
    def test_um_evento_isolado_nao_vira_apontamento(self) -> None:
        """Travar uma vez em oito voltas é acaso, e não se treina acaso."""
        voltas = [_volta(travar_em=APEX_1_M - 40.0)] + [_volta() for _ in range(7)]
        reports = diagnose_corners(voltas)
        assert "travamento" not in _tipos(reports, 1)

    def test_o_habito_vira_apontamento(self) -> None:
        voltas = [_volta(travar_em=APEX_1_M - 40.0) for _ in range(6)]
        reports = diagnose_corners(voltas)
        assert "travamento" in _tipos(reports, 1)

    def test_duas_voltas_com_um_evento_nao_bastam(self) -> None:
        """50% de recorrência com uma ocorrência só ainda é uma ocorrência.

        É o caso que o limiar de fração sozinho deixaria passar, e é justamente
        o começo de uma sessão — quando a tela é mais lida e menos confiável.
        """
        voltas = [_volta(travar_em=APEX_1_M - 40.0), _volta()]
        reports = diagnose_corners(voltas)
        assert "travamento" not in _tipos(reports, 1)
        assert MIN_OCCURRENCES == 2


class TestTextoDoApontamento:
    def test_a_linha_diz_a_curva_a_acao_a_medida_e_a_frequencia(self) -> None:
        voltas = [_volta(patinar_em=APEX_2_M + 40.0) for _ in range(6)]
        reports = diagnose_corners(voltas)
        linhas = [linha for r in reports if r.number == 2 for linha in r.as_lines()]

        assert linhas, "a curva 2 deveria ter apontamento"
        linha = next(x for x in linhas if "acelerador" in x)
        assert linha.startswith("curva 2 — ")
        assert "de 6 voltas" in linha

    def test_o_conselho_de_travamento_muda_com_a_pressao(self) -> None:
        """A mesma medição, dois diagnósticos.

        Travar com o pedal no fundo é excesso de pressão; travar sem pressão
        cheia é freada começando cedo, com a dianteira ainda leve. Um conselho
        só para os dois casos estaria errado em metade das vezes.
        """
        cheia = diagnose_corners(
            [_volta(travar_em=APEX_1_M - 20.0, freada_em=APEX_1_M - 20.0)
             for _ in range(6)]
        )
        linha_cheia = next(
            linha
            for r in cheia if r.number == 1
            for linha in r.as_lines()
            if "pressão" in linha or "pedal no fundo" in linha
        )
        assert "menos pressão" in linha_cheia


class TestOrdenacao:
    def test_a_curva_mais_grave_vem_primeiro(self) -> None:
        """Quem lê para em cima; o teto de curvas mostradas depende disso."""
        voltas = [
            _volta(travar_em=APEX_1_M - 40.0, patinar_em=APEX_1_M + 40.0)
            if i % 6 == 0
            else _volta(travar_em=APEX_2_M - 40.0, patinar_em=APEX_2_M + 40.0)
            for i in range(6)
        ]
        reports = diagnose_corners(voltas)
        assert _numeros(reports)[0] == 2


class TestBordas:
    def test_sem_voltas_nao_ha_diagnostico(self) -> None:
        assert diagnose_corners([]) == []

    def test_volta_sem_curva_nao_inventa_apontamento(self) -> None:
        reta = [
            TelemetryPoint(
                elapsed_ms=i * 30, distance_m=float(i * 2), speed_kmh=200.0,
                rpm=6000.0, gear=6, throttle=100.0, brake=0.0, fuel_level=40.0,
                tire_temp_fl=85.0, tire_temp_fr=85.0,
                tire_temp_rl=85.0, tire_temp_rr=85.0,
                position_x=float(i * 2), position_z=0.0,
                g_lateral=0.0, g_longitudinal=0.0,
                suspension_fl=0.0, suspension_fr=0.0,
                suspension_rl=0.0, suspension_rr=0.0,
                tire_slip_fl=55.5, tire_slip_fr=55.5,
                tire_slip_rl=55.5, tire_slip_rr=55.5,
                turbo_boost=1.0, oil_temp=95.0, water_temp=90.0,
            )
            for i in range(300)
        ]
        assert diagnose_corners([reta, reta, reta]) == []

    def test_curva_limpa_nao_aparece(self) -> None:
        """Listar "curva 1: nada a apontar" enche a tela com o que não pede ação."""
        reports = diagnose_corners([_volta() for _ in range(6)])
        assert "travamento" not in _tipos(reports, 1)
        assert "patinagem" not in _tipos(reports, 1)
