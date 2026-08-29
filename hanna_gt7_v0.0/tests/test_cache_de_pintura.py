"""
O traçado memorizado: rápido sem ficar velho.

Guardar o desenho numa imagem trocou 167 ms por movimento de cursor por 9 — mas
trocou também um defeito lento por um defeito **mentiroso**: imagem que não é
descartada quando os dados mudam mostra a volta anterior com cara de atual, e
nada na tela diz que está errado. Numa ferramenta de telemetria isso é pior que
lentidão.

Por isso o que se testa aqui não é a velocidade, é a **invalidação**: cada
mutador que muda o desenho tem de descartar a imagem, e `set_cursor` — que é a
razão de o cache existir — não pode descartar.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="são widgets Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import DARK_THEME  # noqa: E402
from gt7app.widgets.charts import DistanceChart, Series  # noqa: E402
from gt7app.widgets.gforce import GForceDiagram  # noqa: E402
from gt7app.widgets.trackmap import TrackMap, TrackMarker, TrackPath  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


#: Tema de verdade, e não um dublê com só a paleta. Estes testes **pintam**,
#: e a pintura usa a escala tipográfica além das cores — um dublê parcial
#: quebraria dentro do `paintEvent`, onde a mensagem de erro do Qt é bem pior
#: que a de um `AttributeError` comum.
TEMA = DARK_THEME


def _serie(deslocamento: float = 0.0) -> list[Series]:
    return [
        Series("s", "#4f7cff", [(float(i), float(i) + deslocamento) for i in range(50)])
    ]


def _pintado(widget) -> None:  # noqa: ANN001
    """Força uma pintura, que é quando a imagem memorizada nasce."""
    from PySide6.QtGui import QPixmap

    widget.render(QPixmap(widget.size()))


def _grafico() -> DistanceChart:
    chart = DistanceChart(TEMA, "t")
    chart.resize(400, 150)
    chart.set_series(_serie())
    _pintado(chart)
    return chart


class TestOCacheEDescartadoQuandoODesenhoMuda:
    """Um mutador por teste: a lista de mutadores cresce, e um esquecido aqui
    vira volta velha na tela lá."""

    @pytest.mark.parametrize(
        ("nome", "acao"),
        [
            ("set_series", lambda c: c.set_series(_serie(100.0))),
            ("set_markers", lambda c: c.set_markers([(10.0, "C1", "#fff")])),
            ("set_title", lambda c: c.set_title("outro título")),
            ("set_x_unit", lambda c: c.set_x_unit("s")),
            ("set_x_window", lambda c: c.set_x_window((0.0, 10.0))),
            ("clear", lambda c: c.clear()),
        ],
    )
    def test_mutador_descarta_a_imagem(
        self, app: QApplication, nome: str, acao  # noqa: ANN001, ARG002
    ) -> None:
        chart = _grafico()
        assert chart._backdrop is not None, "a imagem deveria existir antes"  # noqa: SLF001
        acao(chart)
        assert chart._backdrop is None, f"{nome} não descartou a imagem"  # noqa: SLF001

    def test_redimensionar_refaz_a_imagem_no_tamanho_novo(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """Aqui a garantia é o **resultado**, não o mecanismo.

        `resizeEvent` descarta a imagem, mas ele só chega quando há laço de
        eventos rodando; a rede de segurança é a chave (largura, altura,
        densidade) conferida na pintura. Testar a imagem estar `None` logo
        depois do `resize` testaria qual dos dois agiu, e passaria a falhar se
        um deles fosse removido mesmo com o outro cobrindo o caso.
        """
        chart = _grafico()
        chart.resize(700, 150)
        _pintado(chart)

        largura_logica = chart._backdrop.width() / chart._backdrop.devicePixelRatio()  # noqa: SLF001
        assert largura_logica == pytest.approx(700, abs=1)

    def test_a_imagem_nova_reflete_os_dados_novos(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """A prova de que a invalidação serve para alguma coisa: os pixels
        mudam. Sem isto, um `_backdrop = None` que fosse ignorado na pintura
        passaria em todos os testes acima."""
        chart = _grafico()
        antes = chart._backdrop.toImage()  # noqa: SLF001

        chart.set_series(
            [Series("s", "#4f7cff", [(float(i), 500.0 - i) for i in range(50)])]
        )
        _pintado(chart)
        depois = chart._backdrop.toImage()  # noqa: SLF001

        assert antes != depois, "a imagem não acompanhou a troca de série"


class TestOCursorNaoDescarta:
    """É a razão inteira de o cache existir."""

    def test_mover_o_cursor_reaproveita_a_imagem(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        chart = _grafico()
        imagem = chart._backdrop  # noqa: SLF001

        for x in (5.0, 12.0, 33.0, 48.0):
            chart.set_cursor(x)
            _pintado(chart)
            assert chart._backdrop is imagem, (  # noqa: SLF001
                "a imagem foi refeita ao mover o cursor — o cache não serve "
                "para nada assim"
            )

    def test_travar_o_cursor_tambem_reaproveita(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        chart = _grafico()
        imagem = chart._backdrop  # noqa: SLF001
        chart.set_cursor_locked(True)
        _pintado(chart)
        assert chart._backdrop is imagem  # noqa: SLF001


class TestDensidadeDeTela:
    """Num MacBook a tela tem o dobro dos pixels. Gerar a imagem no tamanho
    lógico trocaria lentidão por traço borrado, que não seria melhoria."""

    def test_a_imagem_acompanha_a_densidade(
        self, app: QApplication, monkeypatch  # noqa: ANN001, ARG002
    ) -> None:
        """A densidade é **forçada** para 2×, e não lida da tela.

        A tela virtual dos testes é 1×, e ali multiplicar por 1 ou não
        multiplicar dá o mesmo resultado — verificado por mutação: apagar a
        conta inteira passava em todos os testes. Fingindo 2× o teste passa a
        distinguir os dois casos, que é a única coisa que ele existe para fazer.
        """
        chart = DistanceChart(TEMA, "t")
        chart.resize(400, 150)
        chart.set_series(_serie())
        monkeypatch.setattr(type(chart), "devicePixelRatioF", lambda _self: 2.0)
        _pintado(chart)

        imagem = chart._backdrop  # noqa: SLF001
        assert imagem.devicePixelRatio() == pytest.approx(2.0)
        assert imagem.width() == 800
        assert imagem.height() == 300

    def test_mudar_de_monitor_refaz_a_imagem(
        self, app: QApplication, monkeypatch  # noqa: ANN001, ARG002
    ) -> None:
        """Arrastar a janela de um monitor comum para um Retina muda a escala
        do dispositivo sem mudar o tamanho lógico. Sem a densidade na chave, a
        imagem antiga seria reaproveitada e apareceria borrada."""
        chart = _grafico()
        antiga = chart._backdrop  # noqa: SLF001

        monkeypatch.setattr(type(chart), "devicePixelRatioF", lambda _self: 2.0)
        _pintado(chart)

        assert chart._backdrop is not antiga  # noqa: SLF001
        assert chart._backdrop.width() == 800  # noqa: SLF001


class TestMapaEForcaG:
    """Os outros dois widgets pesados, pelo mesmo padrão."""

    def _mapa(self) -> TrackMap:
        mapa = TrackMap(TEMA, height=200)
        mapa.resize(300, 200)
        mapa.set_paths([
            TrackPath(
                label="volta",
                color="#4f7cff",
                points=[(float(i), float(i % 10)) for i in range(200)],
                distances=[float(i) for i in range(200)],
            )
        ])
        _pintado(mapa)
        return mapa

    def test_o_mapa_descarta_ao_trocar_o_tracado(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        mapa = self._mapa()
        assert mapa._backdrop is not None  # noqa: SLF001
        mapa.set_paths([
            TrackPath(label="outra", color="#fff", points=[(0.0, 0.0), (1.0, 1.0)])
        ])
        assert mapa._backdrop is None  # noqa: SLF001

    def test_o_mapa_descarta_ao_trocar_as_marcas(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        mapa = self._mapa()
        mapa.set_markers([TrackMarker(x=1.0, z=1.0, color="#fff", label="C1")])
        assert mapa._backdrop is None  # noqa: SLF001

    def test_o_cursor_do_mapa_reaproveita(self, app: QApplication) -> None:  # noqa: ARG002
        mapa = self._mapa()
        imagem = mapa._backdrop  # noqa: SLF001
        mapa.set_cursor(50.0)
        _pintado(mapa)
        assert mapa._backdrop is imagem  # noqa: SLF001

    def test_a_forca_g_descarta_ao_trocar_a_nuvem(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        diagrama = GForceDiagram(TEMA, height=200)
        diagrama.resize(200, 200)
        diagrama.set_points([(0.1 * i, 0.05 * i) for i in range(50)])
        _pintado(diagrama)
        assert diagrama._backdrop is not None  # noqa: SLF001

        diagrama.set_points([(0.0, 0.0)])
        assert diagrama._backdrop is None  # noqa: SLF001

    def test_a_bola_da_forca_g_reaproveita_a_nuvem(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """A bola é o único elemento que se mexe com o cursor."""
        diagrama = GForceDiagram(TEMA, height=200)
        diagrama.resize(200, 200)
        diagrama.set_points([(0.1 * i, 0.05 * i) for i in range(50)])
        _pintado(diagrama)
        imagem = diagrama._backdrop  # noqa: SLF001

        diagrama.set_current((1.0, 0.5))
        _pintado(diagrama)
        assert diagrama._backdrop is imagem  # noqa: SLF001

    def test_trocar_a_escala_da_forca_g_descarta(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        diagrama = GForceDiagram(TEMA, height=200)
        diagrama.resize(200, 200)
        diagrama.set_points([(0.1 * i, 0.05 * i) for i in range(50)])
        _pintado(diagrama)
        diagrama.set_scale(5.0)
        assert diagrama._backdrop is None  # noqa: SLF001
