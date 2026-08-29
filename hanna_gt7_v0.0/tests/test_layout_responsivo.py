"""
A janela encolhe sem cortar nada.

O relato foi "as abas do programa estão sendo cortadas quando redimensiona a
janela", e a medição mostrou que a Análise de stint **já nascia cortada**: pedia
1410 px de largura mínima, dos quais 1362 eram o cabeçalho, e a janela abre com
1160 disponíveis. Como a rolagem horizontal estava desligada, o que passava
disso não era rolável — era invisível.

A causa era somar: um `QHBoxLayout` de linha única tem largura mínima igual à
**soma** dos filhos, então cada seletor novo empurrava a página para além da
janela. Estes testes trancam o contrário: nenhuma página pode exigir mais do
que a janela mínima oferece, e acrescentar um seletor não pode voltar a ser
capaz de cortar a tela.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="é layout Qt")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7app.shell import SIDEBAR_WIDTH, WINDOW_MIN_W  # noqa: E402
from gt7app.widgets.cards import MetricCard, MetricGrid, PageHeader  # noqa: E402
from gt7app.widgets.flow import FlowLayout, FlowWidget, labelled  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from tests.conftest import dispose_window  # noqa: E402

#: Páginas cuja largura mínima é dominada por uma **tabela**, que tem mínimo
#: legítimo por ter colunas. Para elas a resposta certa é a barra de rolagem,
#: não espremer as colunas até ninguém conseguir ler.
PAGINAS_COM_TABELA = {"history"}


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def janela(app: QApplication, tmp_path):  # noqa: ANN001, ARG001
    settings = Settings()
    settings.storage.database_path = tmp_path / "t.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"
    core = build_core(settings)
    try:
        window = build_gui(core)
        window.show()
        yield window
        dispose_window(window)
    finally:
        core.close()


class TestNenhumaPaginaExcedeAJanelaMinima:
    def test_as_paginas_cabem_na_menor_janela(self, janela) -> None:  # noqa: ANN001
        """O defeito relatado, medido: 1410 px pedidos, 1160 disponíveis."""
        disponivel = WINDOW_MIN_W - SIDEBAR_WIDTH
        grandes = {
            p.page_id: p.minimumSizeHint().width()
            for p in janela._pages  # noqa: SLF001
            if p.page_id not in PAGINAS_COM_TABELA
            and p.minimumSizeHint().width() > disponivel
        }
        assert not grandes, (
            f"páginas mais largas que os {disponivel} px da janela mínima: {grandes}"
        )

    def test_a_rolagem_horizontal_existe_como_rede_de_seguranca(
        self, janela  # noqa: ANN001
    ) -> None:
        """Para o que não couber — uma tabela larga —, rolar é a saída honesta.

        Desligada, o conteúdo que passa da largura não fica difícil de alcançar:
        fica **inalcançável**, e nada na tela diz que ele existe.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QScrollArea

        areas = janela.findChildren(QScrollArea)
        assert areas, "as páginas deveriam estar dentro de áreas roláveis"
        for area in areas:
            assert (
                area.horizontalScrollBarPolicy()
                != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

    def test_a_janela_tem_um_piso_honesto(self, janela) -> None:  # noqa: ANN001
        """O Qt calculava 260 px e deixava encolher até nada ser legível."""
        assert janela.minimumSize().width() >= 800
        assert janela.minimumSize().height() >= 500


class TestFlowLayout:
    def test_a_largura_minima_e_a_do_maior_item_e_nao_a_soma(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """É a linha que conserta o defeito."""
        barra = FlowWidget(spacing=8)
        larguras = (230, 200, 170, 170)
        for largura in larguras:
            combo = QComboBox()
            combo.setMinimumWidth(largura)
            barra.addWidget(combo)

        assert barra.minimumSizeHint().width() == max(larguras)
        assert barra.minimumSizeHint().width() < sum(larguras)

    def test_quebra_em_mais_linhas_quando_a_faixa_aperta(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        barra = FlowWidget(spacing=8)
        for _ in range(4):
            combo = QComboBox()
            combo.setMinimumWidth(200)
            barra.addWidget(combo)

        uma_linha = barra.heightForWidth(2000)
        apertado = barra.heightForWidth(450)
        assert apertado > uma_linha, (
            "com 450 px os quatro combos de 200 não cabem numa linha só"
        )

    def test_o_rotulo_nao_se_separa_do_campo(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """Um "De:" órfão no fim de uma linha, com o combo dele no começo da
        seguinte, parece que falta alguma coisa — e falta, do lado errado."""
        combo = QComboBox()
        combo.setMinimumWidth(170)
        bloco = labelled("De:", combo)

        rotulos = [x.text() for x in bloco.findChildren(QLabel)]
        assert "De:" in rotulos
        assert combo in bloco.findChildren(QComboBox)
        # O bloco é **um** item para quem quebra linha.
        barra = FlowWidget(spacing=8)
        barra.addWidget(bloco)
        assert barra.flow.count() == 1

    def test_medir_a_altura_nao_mexe_na_geometria(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """`heightForWidth` é consulta, não comando: se ela reposicionasse os
        itens, perguntar a altura durante um cálculo de layout embaralharia a
        tela no meio do cálculo."""
        barra = FlowWidget(spacing=8)
        for _ in range(3):
            combo = QComboBox()
            combo.setMinimumWidth(150)
            barra.addWidget(combo)
        barra.resize(600, 40)
        app.processEvents()

        antes = [barra.flow.itemAt(i).geometry() for i in range(barra.flow.count())]
        barra.heightForWidth(200)
        depois = [barra.flow.itemAt(i).geometry() for i in range(barra.flow.count())]
        assert antes == depois


class TestCabecalhoQueEmpilha:
    def _cabecalho(self) -> PageHeader:
        cabecalho = PageHeader("Análise de stint", "um subtítulo bem comprido " * 2)
        for texto in ("Pista:", "Carro:", "De:", "a"):
            combo = QComboBox()
            combo.setMinimumWidth(200)
            cabecalho.add_action(labelled(texto, combo))
        # Mostrado de propósito: `resizeEvent` — que é quem decide entre lado a
        # lado e empilhado — não é entregue a um widget que nunca apareceu, e o
        # teste estaria medindo um cabeçalho que nunca rodou a decisão.
        cabecalho.show()
        return cabecalho

    def test_lado_a_lado_quando_cabe(self, app: QApplication) -> None:  # noqa: ARG002
        cabecalho = self._cabecalho()
        cabecalho.resize(2400, 80)
        app.processEvents()
        assert not cabecalho._stacked  # noqa: SLF001

    def test_empilha_quando_o_subtitulo_disputa_a_faixa(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """O subtítulo pede ~460 px, e sem empilhar os seletores quebravam cedo
        demais — em janela larga, com espaço de sobra logo abaixo."""
        cabecalho = self._cabecalho()
        cabecalho.resize(900, 80)
        app.processEvents()
        assert cabecalho._stacked  # noqa: SLF001

    def test_a_troca_de_modo_nao_entra_em_laco(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """Mexer no layout dentro de `resizeEvent` dispara outro `resizeEvent`.

        Sem a guarda de transição os dois modos alternariam para sempre, e o
        sintoma seria a janela travando ao ser arrastada — não um erro.
        """
        cabecalho = self._cabecalho()
        for largura in (2400, 900, 2400, 700, 2400):
            cabecalho.resize(largura, 80)
            app.processEvents()
        assert not cabecalho._stacked  # noqa: SLF001


class TestGradeDeMetricas:
    def test_reflui_em_vez_de_exigir_a_soma(self, app: QApplication) -> None:  # noqa: ARG002
        """O nome sempre disse "reflui"; a implementação não refluía."""
        # Títulos de verdade. Com "A" e "B" cada cartão mede 56 px, cinco
        # cabem em qualquer lugar e o teste passaria sem exercitar nada.
        grade = MetricGrid(columns=5)
        for chave, rotulo in (
            ("laps", "VOLTAS"),
            ("best", "MELHOR"),
            ("dev", "DESVIO"),
            ("trend", "TENDÊNCIA"),
            ("err", "PERDAS ADERÊNCIA"),
        ):
            grade.add_card(chave, MetricCard(rotulo, "s"))

        largo = grade.heightForWidth(1500)
        estreito = grade.heightForWidth(500)
        assert estreito > largo, "a grade deveria ganhar linhas ao apertar"

    def test_as_linhas_saem_equilibradas(self, app: QApplication) -> None:  # noqa: ARG002
        """Cinco cartões numa faixa que comporta quatro dão 3 + 2, não 4 + 1.

        Um cartão sozinho na segunda linha se estica pela largura toda e lê
        como se fosse mais importante que os outros quatro.
        """
        grade = MetricGrid(columns=5)
        for chave, rotulo in (
            ("laps", "VOLTAS"),
            ("best", "MELHOR"),
            ("dev", "DESVIO"),
            ("trend", "TENDÊNCIA"),
            ("err", "PERDAS ADERÊNCIA"),
        ):
            grade.add_card(chave, MetricCard(rotulo, "s"))

        maior = max(
            grade._flow.itemAt(i).minimumSize().width()  # noqa: SLF001
            for i in range(grade._flow.count())  # noqa: SLF001
        )
        # Faixa que comporta exatamente quatro cartões.
        faixa = 4 * (maior + 12)
        linhas = grade._flow._split_into_rows(faixa)  # noqa: SLF001
        assert [len(x) for x in linhas] == [3, 2]

    def test_columns_continua_sendo_o_teto(self, app: QApplication) -> None:  # noqa: ARG002
        """Cinco cartões pedidos são cinco quando cabem — a intenção de quem
        montou a tela não vira sugestão só porque o layout ficou flexível."""
        grade = MetricGrid(columns=5)
        for chave, rotulo in (
            ("laps", "VOLTAS"),
            ("best", "MELHOR"),
            ("dev", "DESVIO"),
            ("trend", "TENDÊNCIA"),
            ("err", "PERDAS ADERÊNCIA"),
        ):
            grade.add_card(chave, MetricCard(rotulo, "s"))

        uma_linha = max(c.sizeHint().height() for c in grade.cards.values())
        assert grade.heightForWidth(4000) <= uma_linha + 4


class TestFlowLayoutIsolado:
    def test_sem_itens_nao_quebra(self, app: QApplication) -> None:  # noqa: ARG002
        layout = FlowLayout(spacing=8)
        assert layout.heightForWidth(300) >= 0
        assert layout.count() == 0

    def test_um_item_maior_que_a_faixa_fica_sozinho(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        barra = FlowWidget(spacing=8)
        gordo = QComboBox()
        gordo.setMinimumWidth(600)
        magro = QComboBox()
        magro.setMinimumWidth(80)
        barra.addWidget(gordo)
        barra.addWidget(magro)

        # Em 300 px nem o primeiro cabe; ele fica na linha dele e o segundo na
        # seguinte, em vez de os dois serem espremidos abaixo do mínimo.
        assert barra.heightForWidth(300) > barra.heightForWidth(2000)
