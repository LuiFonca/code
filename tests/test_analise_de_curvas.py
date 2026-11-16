"""
Análise de curvas — voltas montadas trecho a trecho, com resposta conhecida.

A volta sintética é uma sequência de retas, frenagens e curvas de comprimento,
velocidade e força lateral escolhidos. Assim cada asserção tem um número certo
para comparar, em vez de "parece razoável".
"""

import pytest

from src.domain.services.corner_analysis import (
    EQUILIBRIO_NEUTRO,
    EQUILIBRIO_SOBRESTERCO,
    EQUILIBRIO_SUBESTERCO,
    GRAVIDADE,
    compare_corners,
    delta_trace,
    detect_corners,
)
from tests.conftest import make_point


def _volta(trechos, fator_guinada=1.0, passo_m=2.0):
    """Monta uma volta a partir de `(comprimento_m, velocidade, g_lateral, freio)`.

    `fator_guinada` multiplica a guinada que a curva exigiria: 1.0 é carro
    neutro, acima gira demais (traseira), abaixo gira de menos (frente).
    """
    pontos, distancia, tempo_ms = [], 0.0, 0.0
    for comprimento, velocidade, g_lat, freio in trechos:
        n = int(comprimento / passo_m)
        for _ in range(n):
            v = velocidade / 3.6
            guinada = (g_lat * GRAVIDADE / v) * fator_guinada if v > 0 else 0.0
            ponto = make_point(
                0, 1, 0, 0.0,
                elapsed_ms=int(tempo_ms), speed_kmh=velocidade,
                g_lateral=g_lat, brake=freio,
                position_x=distancia, position_z=0.0, yaw_rate=guinada,
            )
            ponto.distance_m = distancia
            pontos.append(ponto)
            distancia += passo_m
            tempo_ms += passo_m / v * 1000.0
    return pontos


#          comprimento  vel   g_lat  freio
PISTA = [
    (300.0, 200.0, 0.0, 0.0),     # reta
    (80.0, 140.0, 0.0, 80.0),     # frenagem
    (100.0, 90.0, 1.2, 0.0),      # curva 1, direita
    (250.0, 180.0, 0.0, 0.0),     # reta
    (60.0, 150.0, 0.0, 60.0),     # frenagem
    (120.0, 110.0, -1.0, 0.0),    # curva 2, esquerda
    (200.0, 190.0, 0.0, 0.0),     # reta
]


def test_detecta_as_curvas_da_volta():
    curvas = detect_corners(_volta(PISTA))
    assert len(curvas) == 2
    assert [c.index for c in curvas] == [1, 2]


def test_direcao_e_velocidade_minima():
    c1, c2 = detect_corners(_volta(PISTA))
    assert c1.direction == "direita" and c2.direction == "esquerda"
    assert c1.min_speed_kmh == pytest.approx(90.0)
    assert c2.min_speed_kmh == pytest.approx(110.0)
    assert c1.peak_g == pytest.approx(1.2)


def test_posicao_da_curva():
    c1, _ = detect_corners(_volta(PISTA))
    assert c1.start_m == pytest.approx(380.0, abs=3.0)
    assert c1.length_m == pytest.approx(100.0, abs=5.0)


def test_ponto_de_frenagem_e_o_inicio_do_trecho_de_freio():
    c1, c2 = detect_corners(_volta(PISTA))
    assert c1.brake_point_m == pytest.approx(300.0, abs=3.0)
    assert c2.brake_point_m == pytest.approx(730.0, abs=3.0)


def test_curva_sem_frenagem_nao_inventa_ponto():
    pista = [(300.0, 150.0, 0.0, 0.0), (100.0, 150.0, 0.8, 0.0), (200.0, 150.0, 0.0, 0.0)]
    (curva,) = detect_corners(_volta(pista))
    assert curva.brake_point_m is None


def test_reta_com_correcao_de_volante_nao_vira_curva():
    pista = [(500.0, 200.0, 0.15, 0.0)]
    assert detect_corners(_volta(pista)) == []


def test_solavanco_curto_nao_vira_curva():
    pista = [(300.0, 200.0, 0.0, 0.0), (6.0, 200.0, 0.9, 0.0), (300.0, 200.0, 0.0, 0.0)]
    assert detect_corners(_volta(pista)) == []


def test_s_do_mesmo_lado_e_fundido_e_de_lados_opostos_nao():
    mesmo = [(200.0, 150.0, 0.0, 0.0), (60.0, 120.0, 0.9, 0.0), (10.0, 120.0, 0.1, 0.0),
             (60.0, 120.0, 0.9, 0.0), (200.0, 150.0, 0.0, 0.0)]
    opostos = [(200.0, 150.0, 0.0, 0.0), (60.0, 120.0, 0.9, 0.0), (10.0, 120.0, 0.1, 0.0),
               (60.0, 120.0, -0.9, 0.0), (200.0, 150.0, 0.0, 0.0)]
    assert len(detect_corners(_volta(mesmo))) == 1
    assert len(detect_corners(_volta(opostos))) == 2


# ------------------------------------------------------- equilíbrio
@pytest.mark.parametrize("fator, esperado", [
    (1.0, EQUILIBRIO_NEUTRO),
    (1.4, EQUILIBRIO_SOBRESTERCO),
    (0.6, EQUILIBRIO_SUBESTERCO),
])
def test_equilibrio_pela_guinada(fator, esperado):
    c1, _ = detect_corners(_volta(PISTA, fator_guinada=fator))
    assert c1.balance == esperado


def test_sem_guinada_gravada_o_equilibrio_fica_indefinido():
    """Voltas anteriores ao schema v10 não têm o dado — e isso não é 'neutro'."""
    pontos = _volta(PISTA)
    for p in pontos:
        p.yaw_rate = None
    assert all(c.balance is None for c in detect_corners(pontos))


# ------------------------------------------------------- comparação
def test_volta_identica_nao_ganha_nem_perde():
    ref = _volta(PISTA)
    deltas = compare_corners(ref, _volta(PISTA))
    assert len(deltas) == 2
    for d in deltas:
        assert d.time_delta_ms == pytest.approx(0.0, abs=1.0)
        assert d.min_speed_delta_kmh == pytest.approx(0.0)


def test_curva_mais_lenta_aparece_como_tempo_perdido_nela():
    """Só a curva 1 muda; a perda tem que aparecer na 1 e não na 2."""
    lenta = list(PISTA)
    lenta[2] = (100.0, 75.0, 1.2, 0.0)
    d1, d2 = compare_corners(_volta(PISTA), _volta(lenta))

    # 100 m a 75 km/h contra 100 m a 90 km/h: 4,8 s contra 4,0 s.
    assert d1.time_delta_ms == pytest.approx(800.0, abs=40.0)
    assert d1.min_speed_delta_kmh == pytest.approx(-15.0)
    assert d2.time_delta_ms == pytest.approx(0.0, abs=5.0)


def test_frear_mais_tarde_aparece_em_metros():
    tarde = list(PISTA)
    tarde[0] = (320.0, 200.0, 0.0, 0.0)
    tarde[1] = (60.0, 140.0, 0.0, 80.0)
    d1, _ = compare_corners(_volta(PISTA), _volta(tarde))
    assert d1.brake_point_delta_m == pytest.approx(20.0, abs=3.0)


# ------------------------------------------------------- mapa de ganho e perda
def test_mapa_localiza_a_perda_no_trecho_certo():
    lenta = list(PISTA)
    lenta[2] = (100.0, 75.0, 1.2, 0.0)
    pedacos = delta_trace(_volta(PISTA), _volta(lenta), segments=40)
    assert pedacos

    pior = max(pedacos, key=lambda p: p[1])
    x_medio = sum(x for x, _ in pior[0]) / len(pior[0])
    assert 380.0 <= x_medio <= 480.0, f"a maior perda caiu em x={x_medio:.0f}"
    assert sum(g for _, g in pedacos) == pytest.approx(800.0, abs=60.0)


def test_mapa_de_volta_identica_e_todo_zero():
    pedacos = delta_trace(_volta(PISTA), _volta(PISTA), segments=30)
    assert all(abs(g) < 1.0 for _, g in pedacos)
