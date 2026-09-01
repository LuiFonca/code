"""
O que está escrito na célula.

Este arquivo existe por causa de uma medição desconfortável: `history.py` tinha
**87% de cobertura de linha** enquanto exibia três definições contraditórias de
setor. O defeito morava em linhas cobertas. Os testes das páginas cobriam ações
— apagar volta, redimensionar, conectar — e nenhum olhava para o **número**.

Foi assim que passaram despercebidos, ao mesmo tempo:

- setores que não somavam o tempo da volta (369 ms a menos);
- a divisa do mapa apontando para um asfalto e a da tabela para outro;
- um recorde novo reescrevendo o tempo de setor de voltas antigas;
- e o roxo de "melhor setor" pintado uma coluna à esquerda, em "Δ melhor", com
  o Setor 3 nunca marcado.

O último só apareceu quando alguém foi conferir o índice. Nenhuma cobertura de
linha pega isso — só ler a célula pega.

A forma dos testes é sempre a mesma: montar voltas cuja resposta se calcula à
mão, abrir a página de verdade e **ler o texto renderizado**. Voltas a
velocidade constante existem para isso — o tempo de cada trecho é proporcional
à distância dele, então o valor esperado sai da aritmética, e não de um valor
capturado de uma execução anterior (que só congelaria o defeito).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="as páginas são Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7app.pages.history import (  # noqa: E402
    COVERAGE_COLUMN,
    FIRST_SECTOR_COLUMN,
    HISTORY_COLUMNS,
)
from gt7core.analytics.sectors import NUM_SECTORS  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap, TelemetryPoint, Track  # noqa: E402

SUZUKA_M = 5807.0
"""Comprimento oficial no catálogo do jogo."""

#: Linhas da tabela. Ela lista da volta mais recente para a mais antiga, então a
#: última gravada — a cortada — fica no topo.
LINHA_CORTADA = 0
LINHA_RECORDE = 1
LINHA_MELHOR_S3 = 2


def _ponto(elapsed_ms: int, distance_m: float) -> TelemetryPoint:
    return TelemetryPoint(
        elapsed_ms=elapsed_ms, distance_m=distance_m, speed_kmh=180.0, rpm=6000.0,
        gear=5, throttle=100.0, brake=0.0, fuel_level=40.0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        position_x=0.0, position_z=0.0, g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.1, suspension_fr=0.1, suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=27.7, tire_slip_fr=27.7, tire_slip_rl=27.7, tire_slip_rr=27.7,
        turbo_boost=1.0, oil_temp=90.0, water_temp=85.0,
    )


#: As divisas interiores, escritas aqui de forma independente de
#: `analytics.sectors` — se as duas discordarem, é sinal de que a régua mudou.
DIVISA_1 = SUZUKA_M * (1 / 3)
DIVISA_2 = SUZUKA_M * (2 / 3)


def _volta_por_setores(
    comprimento_m: float, tempos_ms: tuple[int, int, int], por_trecho: int = 200
) -> list[TelemetryPoint]:
    """Volta cujos setores são **os que se pediu**.

    Cada trecho entre divisas é percorrido a velocidade constante no tempo
    informado, e uma amostra cai exatamente sobre cada divisa — então o tempo de
    setor que o programa calcula é o que entrou aqui, sem arredondamento.

    Isso inverte a direção do teste: o valor esperado é **entrada**, não uma
    dedução que poderia repetir o mesmo erro do código que está sendo verificado.
    """
    trechos = [(0.0, DIVISA_1), (DIVISA_1, DIVISA_2), (DIVISA_2, comprimento_m)]
    pontos: list[TelemetryPoint] = []
    decorrido = 0
    for (inicio, fim), duracao in zip(trechos, tempos_ms, strict=True):
        for i in range(por_trecho):
            fracao = i / por_trecho
            pontos.append(
                _ponto(
                    decorrido + int(fracao * duracao),
                    inicio + fracao * (fim - inicio),
                )
            )
        decorrido += duracao
    # A amostra final fecha a volta exatamente no fim e no tempo total.
    pontos.append(_ponto(decorrido, comprimento_m))
    return pontos


def _para_ms(texto: str) -> int:
    """`"1:32.345"` de volta para 92345. O caminho inverso do que a tela fez."""
    minutos, resto = texto.split(":")
    segundos, millis = resto.split(".")
    return int(minutos) * 60_000 + int(segundos) * 1000 + int(millis)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class Cenario:
    """Uma janela real, com voltas de resposta conhecida gravadas."""

    def __init__(self, core, window) -> None:  # noqa: ANN001
        self.core = core
        self.window = window

    def page(self, page_id: str):  # noqa: ANN201
        return next(p for p in self.window._pages if p.page_id == page_id)  # noqa: SLF001

    def cell(self, page_id: str, row: int, column: int) -> str:
        item = self.page(page_id)._table.item(row, column)  # noqa: SLF001
        return item.text() if item is not None else ""

    def card(self, page_id: str, key: str) -> str:
        return self.page(page_id)._summary.cards[key]._value.text()  # noqa: SLF001


@pytest.fixture
def cenario(app: QApplication, tmp_path: Path):  # noqa: ANN201, ARG001
    """Suzuka com três voltas limpas e uma cortada.

    As três limpas são a velocidade constante e diferem só no tempo, então o
    setor 1 de cada uma é exatamente um terço do tempo dela — o que torna cada
    número da tabela verificável sem rodar o programa.
    """
    settings = Settings()
    settings.storage.database_path = tmp_path / "n.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"

    core = build_core(settings)
    track_id = core.tracks.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
    core.session_manager.set_track(Track(id=track_id, name="Suzuka Circuit"))

    # A ordem importa: a tabela lista da mais recente para a mais antiga.
    #
    # Os setores são repartidos de propósito. A volta do recorde não é a mais
    # rápida em **todos** os trechos: a linha 1 leva o melhor setor 3. Sem essa
    # separação, o recorde deteria os três melhores e não daria para distinguir
    # o roxo do "melhor setor" do roxo que pinta a linha inteira do recorde.
    agora = datetime.datetime(2026, 3, 1, 10, 0, 0)
    plano = [
        (5820.0, (43_000, 43_000, 43_000)),  # mais lenta em tudo
        (5820.0, (42_500, 42_500, 39_000)),  # melhor setor 3, volta mais lenta
        (5820.0, (41_000, 41_000, 41_000)),  # recorde: 2:03.000
        (4100.0, (30_000, 30_000, 30_000)),  # cortou meia pista: rápida e falsa
    ]
    for indice, (comprimento, tempos) in enumerate(plano):
        core.laps.save(
            Lap(
                id=None, session_id=None, track_id=track_id, car_id=None,
                is_player=True, lap_time_ms=sum(tempos),
                start_time=agora + datetime.timedelta(minutes=indice),
                points=_volta_por_setores(comprimento, tempos),
            )
        )

    window = build_gui(core)
    for page_id in ("history", "driver"):
        window_page = next(p for p in window._pages if p.page_id == page_id)  # noqa: SLF001
        window_page.refresh()

    yield Cenario(core, window)

    window.close()


class TestATabelaDoHistorico:
    def test_o_setor_1_e_um_terco_do_tempo_da_volta(self, cenario: Cenario) -> None:
        """O tempo de setor que a célula mostra é o que a volta gastou ali.

        A volta do recorde foi construída com 41,0 s em cada trecho, e uma
        amostra cai exatamente sobre cada divisa — então não há arredondamento
        para tolerar e a igualdade é exata.
        """
        for i, esperado in enumerate((41_000, 41_000, 41_000)):
            lido = _para_ms(cenario.cell("history", LINHA_RECORDE,
                                         FIRST_SECTOR_COLUMN + i))
            assert lido == esperado, f"setor {i + 1}"

    def test_os_tres_setores_somam_o_tempo_da_volta(self, cenario: Cenario) -> None:
        """A primeira conta que um piloto confere, e a que o mapa quebrava."""
        for linha, total in ((LINHA_RECORDE, 123_000), (LINHA_MELHOR_S3, 124_000)):
            soma = sum(
                _para_ms(cenario.cell("history", linha, FIRST_SECTOR_COLUMN + i))
                for i in range(NUM_SECTORS)
            )
            assert soma == total, f"linha {linha}"

    def test_o_roxo_do_melhor_setor_cai_na_coluna_do_setor(
        self, cenario: Cenario
    ) -> None:
        """O defeito que nenhuma cobertura de linha pegou.

        O índice era `3 + i` escrito à mão, e as colunas de setor começam em 4:
        o destaque de "melhor setor 1" pintava "Δ melhor", e o Setor 3 nunca era
        marcado. Derivar o índice da tupla é o que impede a próxima coluna
        inserida no meio de repetir isso.
        """
        assert HISTORY_COLUMNS[FIRST_SECTOR_COLUMN] == "Setor 1"
        assert HISTORY_COLUMNS[FIRST_SECTOR_COLUMN + NUM_SECTORS - 1] == "Setor 3"
        assert HISTORY_COLUMNS[COVERAGE_COLUMN] == "Traçado"

        pagina = cenario.page("history")
        tabela = pagina._table  # noqa: SLF001
        roxo = pagina.theme.palette.purple

        # A linha do recorde é pintada **inteira** de roxo, de propósito; olhar
        # para ela não distingue os dois usos da cor. A linha do melhor setor 3
        # distingue: ali o roxo só pode estar na coluna do Setor 3.
        pintadas = {
            coluna
            for coluna in range(tabela.columnCount())
            if (item := tabela.item(LINHA_MELHOR_S3, coluna)) is not None
            and item.foreground().color().name().lower() == roxo.lower()
        }
        assert pintadas == {FIRST_SECTOR_COLUMN + NUM_SECTORS - 1}, (
            f"esperado só a coluna do Setor 3, veio {sorted(pintadas)}"
        )

    def test_a_cobertura_do_tracado_aparece_como_numero(
        self, cenario: Cenario
    ) -> None:
        """O número é mostrado, e não convertido em ícone de "ok/suspeita":
        é com ele na mão que os limiares de `domain.validity` vão poder ser
        apertados contra telemetria real."""
        assert cenario.cell("history", LINHA_RECORDE, COVERAGE_COLUMN) == "100.2%"
        assert cenario.cell("history", LINHA_CORTADA, COVERAGE_COLUMN) == "70.6%"

    def test_o_recorde_ignora_a_volta_cortada(self, cenario: Cenario) -> None:
        """A volta de 90 s é a mais rápida da tabela e **não** é o recorde.

        Ela percorreu 70,6% de Suzuka; o tempo dela é real e a distância também,
        só que juntos não descrevem uma volta na pista.
        """
        assert cenario.card("history", "best") == "2:03.000"
        assert cenario.card("history", "count") == "4"

    def test_a_volta_cortada_continua_listada(self, cenario: Cenario) -> None:
        """Marcada, e não escondida: é um fato da sessão. O que ela perde é
        entrar no recorde e no perfil, não existir."""
        assert cenario.cell("history", LINHA_CORTADA, 2) == "1:30.000"

    def test_o_rodape_diz_de_onde_vem_a_regua(self, cenario: Cenario) -> None:
        """Catálogo e mediana têm confiabilidades diferentes; esconder a
        distinção faria as duas parecerem igualmente sólidas."""
        nota = cenario.page("history")._note.text()  # noqa: SLF001
        assert "5807" in nota
        assert "oficial" in nota


class TestOPerfilDoPiloto:
    def test_a_volta_cortada_fica_de_fora_do_perfil(self, cenario: Cenario) -> None:
        """Ela tem menos curvas e menos frenagens do que uma volta inteira, e
        entra na média puxando tudo para o lado errado."""
        pagina = cenario.page("driver")
        assert pagina._summary.cards["laps"]._value.text() == "3"  # noqa: SLF001

    def test_e_o_descarte_e_dito_na_tela(self, cenario: Cenario) -> None:
        """Descartar em silêncio é o que este projeto não faz: o piloto veria
        três voltas onde gravou quatro, sem explicação."""
        badge = cenario.page("driver")._badge  # noqa: SLF001
        # `isVisible()` é falso enquanto a janela não for exibida, e a suíte
        # nunca a exibe. `isHidden()` responde o que interessa: alguém mandou
        # esconder este aviso?
        assert not badge.isHidden()
        assert "incompleta" in badge.text()
