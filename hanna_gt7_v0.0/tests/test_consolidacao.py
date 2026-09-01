"""
Uma implementação por pergunta.

Duas perguntas do projeto estavam respondidas em vários lugares ao mesmo tempo:
"como se escreve um tempo de volta" (cinco cópias) e "qual amostra corresponde a
esta distância" (três). Em ambos os casos as cópias **já tinham divergido** —
não era risco futuro, era defeito presente.

O que este arquivo tranca não é o comportamento de cada função, que os módulos
de origem já testam. É a **unicidade**: uma cópia nova nascendo é o começo da
próxima divergência, e ela nasce de alguém que não sabia que a função já existia.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from gt7core.analytics.lookup import index_at_distance, point_at_distance
from gt7core.domain.formatting import UNKNOWN, format_lap_time
from gt7core.domain.models import TelemetryPoint

RAIZ = pathlib.Path(__file__).resolve().parent.parent

#: A única implementação legítima. Identificada pelo caminho: existe mais de um
#: `formatting.py` no projeto.
CANONICA = RAIZ / "gt7core" / "domain" / "formatting.py"

#: Os pacotes entregues. Testes e ferramentas ficam de fora: um dublê de teste
#: pode legitimamente ter a própria formatação.
ENTREGUES = ("gt7core", "gt7app", "gt7ai", "gt7discord", "gt7voice")


def _fontes() -> list[pathlib.Path]:
    return [
        p
        for pacote in ENTREGUES
        for p in (RAIZ / pacote).rglob("*.py")
        if "__pycache__" not in str(p)
    ]


def _ponto(distancia: float) -> TelemetryPoint:
    return TelemetryPoint(
        elapsed_ms=0, distance_m=distancia, speed_kmh=100.0, rpm=5000.0, gear=4,
        throttle=0.0, brake=0.0, fuel_level=40.0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        position_x=0.0, position_z=0.0, g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.1, suspension_fr=0.1, suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=27.7, tire_slip_fr=27.7, tire_slip_rl=27.7, tire_slip_rr=27.7,
        turbo_boost=1.0, oil_temp=90.0, water_temp=85.0,
    )


class TestUmaFormatacaoDeTempo:
    def test_todos_os_pacotes_apontam_para_a_mesma_funcao(self) -> None:
        """A prova de unicidade é a identidade do objeto, não o resultado.

        Cinco funções idênticas passariam num teste de valor e continuariam
        podendo divergir amanhã. `is` não passa.
        """
        from gt7ai.prompts import format_lap_time as ia
        from gt7app.widgets.selectors import format_lap_time as ui
        from gt7core.analytics.driver import format_lap_time as perfil
        from gt7core.demo import format_lap_time as demo
        from gt7discord.formatting import lap_time as discord

        assert ui is ia is demo is perfil is discord is format_lap_time

    def test_nenhum_pacote_reimplementa_a_formatacao(self) -> None:
        """Uma cópia nova é o começo da próxima divergência.

        A isenção é por **caminho**, não por nome de arquivo: `gt7discord`
        também tem um `formatting.py`, e isentar pelo nome abriria justamente a
        porta por onde a cópia voltaria — foi o que uma mutação mostrou.
        """
        padrao = re.compile(r"divmod\(\s*\w+\s*,\s*60_?000\s*\)")
        culpados = [
            str(p.relative_to(RAIZ))
            for p in _fontes()
            if padrao.search(p.read_text(encoding="utf-8")) and p != CANONICA
        ]
        assert not culpados, f"formatação de tempo reimplementada em: {culpados}"

    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            (92_345, "1:32.345"),
            (3_600_000, "60:00.000"),
            (92_345.7, "1:32.345"),
            (1, "0:00.001"),
        ],
    )
    def test_formato_do_painel(self, entrada, esperado: str) -> None:  # noqa: ANN001
        assert format_lap_time(entrada) == esperado

    @pytest.mark.parametrize("entrada", [0, -1, -92_345, None])
    def test_o_que_nao_e_volta_vira_travessao(self, entrada) -> None:  # noqa: ANN001
        """A divergência que existia: três lugares diziam "—" para `-1` e dois
        imprimiam `-1:59.999`.

        E um dos dois, `analytics.driver`, alimenta o resumo que vai para o
        prompt do engenheiro — que foi instruído a nunca inventar grandeza e
        repetiria o absurdo com toda a confiança.
        """
        assert format_lap_time(entrada) == UNKNOWN


class TestUmaBuscaPorDistancia:
    def test_as_paginas_e_o_acelerador_usam_a_mesma_busca(self) -> None:
        padrao = re.compile(r"def _point_at\(|def _index_at_distance\(")
        culpados = [
            str(p.relative_to(RAIZ))
            for p in _fontes()
            if padrao.search(p.read_text(encoding="utf-8"))
        ]
        assert not culpados, f"busca por distância reimplementada em: {culpados}"

    def test_devolve_a_mais_proxima_e_nao_a_primeira_maior(self) -> None:
        """O alvo quase sempre cai **entre** duas amostras, e a de trás pode
        estar mais perto. Parar na primeira maior ou igual erraria metade das
        vezes por meia amostra — invisível num gráfico, visível no cartão de
        leitura do cursor."""
        pontos = [_ponto(0.0), _ponto(10.0), _ponto(20.0)]
        assert index_at_distance(pontos, 9.9) == 1
        assert index_at_distance(pontos, 4.9) == 0
        assert index_at_distance(pontos, 5.1) == 1

    def test_concorda_com_a_varredura_linear_em_toda_a_faixa(self) -> None:
        """A versão linear era a implementação anterior; a binária só vale se
        devolver exatamente o mesmo ponto, inclusive fora da faixa."""
        import random

        pontos = [_ponto(i * 0.6180339) for i in range(2000)]
        fim = pontos[-1].distance_m

        def linear(distancia: float) -> int:
            return min(
                range(len(pontos)),
                key=lambda i: abs(pontos[i].distance_m - distancia),
            )

        random.seed(11)
        alvos = [-50.0, 0.0, fim, fim + 500.0] + [
            random.uniform(-5.0, fim + 5.0) for _ in range(600)
        ]
        for alvo in alvos:
            achado = index_at_distance(pontos, alvo)
            assert achado is not None
            assert abs(pontos[achado].distance_m - alvo) == abs(
                pontos[linear(alvo)].distance_m - alvo
            ), f"divergiu em {alvo}"

    def test_lista_vazia_devolve_nada(self) -> None:
        assert index_at_distance([], 10.0) is None
        assert point_at_distance([], 10.0) is None

    def test_uma_amostra_so(self) -> None:
        pontos = [_ponto(5.0)]
        assert point_at_distance(pontos, 999.0) is pontos[0]
        assert point_at_distance(pontos, -999.0) is pontos[0]

    def test_e_binaria_de_verdade(self) -> None:
        """Uma varredura linear passaria em todos os testes acima.

        O que separa as duas é o **número de amostras consultadas**: a binária
        olha ~log₂(n), a linear olha n. É essa diferença que tirou o cursor da
        Análise de 5 ms para 0,03 ms, então ela merece um teste — medir tempo de
        relógio seria instável em máquina carregada, e contar acessos responde a
        mesma pergunta de forma determinística.
        """
        acessos = [0]

        class Espiao:
            """Amostra que registra cada leitura da própria distância.

            Não herda de `TelemetryPoint`: `slots=True` impede substituir o
            campo por uma propriedade. A busca só lê `distance_m`, então
            qualquer objeto que ofereça esse atributo serve.
            """

            __slots__ = ("_distancia",)

            def __init__(self, distancia: float) -> None:
                self._distancia = distancia

            @property
            def distance_m(self) -> float:
                acessos[0] += 1
                return self._distancia

        pontos = [Espiao(float(i)) for i in range(4096)]
        index_at_distance(pontos, 2047.5)  # type: ignore[arg-type]

        # 12 iterações (log₂ 4096) mais a comparação final com a amostra
        # anterior. O teto de 20 dá folga para variações da implementação e
        # ainda fica a duas ordens de grandeza dos 4096 de uma varredura.
        assert acessos[0] <= 20, (
            f"a busca consultou {acessos[0]} de 4096 amostras — "
            "isso não é uma busca binária"
        )
