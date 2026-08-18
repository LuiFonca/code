"""
Canal do mapa, cursor travável, resolução do traçado e inibidor de suspensão.

Três dos quatro são interação, e interação é onde o teste automatizado costuma
verificar que o método existe em vez de que ele funciona. O que se prende aqui é
o **comportamento observável**: o mapa muda de modo de pintura, o cursor deixa
de seguir o ponteiro depois do clique e volta a seguir depois do segundo, e a
reamostragem cresce com o tamanho do widget em vez de ficar num teto fixo.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6", reason="são widgets Qt")

from datetime import datetime  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7app.power import KeepAwake  # noqa: E402
from gt7app.widgets.trackmap import TrackMap, TrackPath  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap  # noqa: E402
from gt7core.telemetry.sources.mock import synthetic_lap  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def montado(app: QApplication, tmp_path):  # noqa: ANN001, ARG001
    settings = Settings()
    settings.storage.database_path = tmp_path / "t.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"

    core = build_core(settings)
    try:
        track_id = core.tracks.get_or_create("Interlagos")
        for tempo in (92_000, 93_000):
            core.engine.reset()
            for frame in synthetic_lap(lap_time_ms=tempo):
                core.engine.on_frame(frame)
            core.laps.save(
                Lap(
                    track_id=track_id,
                    lap_time_ms=tempo,
                    start_time=datetime.now(),
                    points=list(core.engine._buffer),  # noqa: SLF001
                )
            )
        window = build_gui(core)
        yield window
        window.close()
    finally:
        core.close()


class TestCanalDoMapa:
    def test_velocidade_usa_gradiente_e_pedais_usa_cor_pronta(self, montado) -> None:  # noqa: ANN001
        """Os dois modos existem porque são duas perguntas.

        Velocidade é "quanto" e pede gradiente; pedais é "qual" e não pode ter
        gradiente — o meio do caminho entre verde e vermelho seria um estado que
        não existiu.
        """
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()

        caminho = pagina._map._paths[0]  # noqa: SLF001
        assert caminho.has_heatmap and not caminho.has_categorical

        pagina._map_channel.setCurrentIndex(1)  # noqa: SLF001

        caminho = pagina._map._paths[0]  # noqa: SLF001
        assert caminho.has_categorical, "pedais tem de trazer cor por ponto"
        assert len(caminho.colors) == len(caminho.points)

    def test_as_tres_cores_dos_pedais_aparecem(self, montado) -> None:  # noqa: ANN001
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._map_channel.setCurrentIndex(1)  # noqa: SLF001

        palette = pagina.theme.palette
        usadas = set(pagina._map._paths[0].colors)  # noqa: SLF001

        assert palette.channel_throttle in usadas, "acelerador em verde"
        assert palette.channel_brake in usadas, "freada em vermelho"
        assert palette.yellow in usadas, "sem pedal nenhum em amarelo"

    def test_freio_ganha_do_acelerador_no_trail_braking(self, montado) -> None:  # noqa: ANN001
        """Pé nos dois é trail braking, e o que se quer ver no mapa é até onde a
        freada se estendeu — pintar de verde esconderia a sobreposição."""
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        palette = pagina.theme.palette

        ponto = pagina._points[0]  # noqa: SLF001
        campos = {f: getattr(ponto, f) for f in ponto.__slots__}
        ambos = type(ponto)(**{**campos, "throttle": 60.0, "brake": 40.0})

        assert pagina._pedal_color(ambos) == palette.channel_brake  # noqa: SLF001

    def test_a_legenda_acompanha_o_modo(self, montado) -> None:  # noqa: ANN001
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        assert pagina._map._legend == []  # noqa: SLF001

        pagina._map_channel.setCurrentIndex(1)  # noqa: SLF001
        assert len(pagina._map._legend) == 3  # noqa: SLF001


class TestCursorTravavel:
    def test_clique_trava_e_o_ponteiro_para_de_mover(self, montado) -> None:  # noqa: ANN001
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._on_click(1200.0)  # noqa: SLF001
        assert pagina._frozen  # noqa: SLF001
        assert pagina._charts[0]._cursor_m == 1200.0  # noqa: SLF001

        pagina._on_hover(2500.0)  # noqa: SLF001
        assert pagina._charts[0]._cursor_m == 1200.0, "travado é travado"  # noqa: SLF001

    def test_segundo_clique_solta(self, montado) -> None:  # noqa: ANN001
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._on_click(1200.0)  # noqa: SLF001
        pagina._on_click(1200.0)  # noqa: SLF001

        assert not pagina._frozen  # noqa: SLF001
        pagina._on_hover(2500.0)  # noqa: SLF001
        assert pagina._charts[0]._cursor_m == 2500.0  # noqa: SLF001

    def test_travado_sai_do_widget_sem_perder_a_leitura(self, montado) -> None:  # noqa: ANN001
        """Sair com o mouse não pode apagar o cursor travado — travar existe
        justamente para poder tirar a mão de cima."""
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._on_click(1200.0)  # noqa: SLF001

        pagina._on_hover_left()  # noqa: SLF001

        assert pagina._charts[0]._cursor_m == 1200.0  # noqa: SLF001

    def test_todos_os_graficos_marcam_o_travamento(self, montado) -> None:  # noqa: ANN001
        """Sem sinal visual, um cursor parado parece a aplicação congelada."""
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._on_click(1200.0)  # noqa: SLF001

        assert all(c._cursor_locked for c in pagina._charts)  # noqa: SLF001

    def test_a_tabela_move_o_cursor_mesmo_travado(self, montado) -> None:  # noqa: ANN001
        """Escolher uma curva na tabela é comando explícito. Ignorá-lo faria a
        tabela parecer quebrada."""
        pagina = montado._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._on_click(10.0)  # noqa: SLF001

        pagina._table.selectRow(0)
        alvo = pagina._x_at_distance(pagina._corners[0].apex_distance_m)  # noqa: SLF001

        assert pagina._charts[0]._cursor_m == pytest.approx(alvo)  # noqa: SLF001

    def test_a_comparacao_trava_igual(self, montado) -> None:  # noqa: ANN001
        pagina = montado._pages[2]  # noqa: SLF001
        pagina.refresh()

        pagina._on_click(1200.0)  # noqa: SLF001
        pagina._on_hover(2500.0)  # noqa: SLF001

        assert pagina._delta_chart._cursor_m == 1200.0  # noqa: SLF001


class TestResolucaoDoMapa:
    def test_o_orcamento_cresce_com_o_widget(self, app: QApplication) -> None:  # noqa: ARG002
        """Era um teto fixo de 1.200 pontos.

        Num mapa grande ele cortava a informação que se quer ver: duas linhas de
        corrida separadas por meio metro caíam dentro do mesmo segmento e viravam
        a mesma linha.
        """
        from gt7app.design.tokens import get_theme

        mapa = TrackMap(get_theme("dark"))
        mapa.resize(300, 200)
        pequeno = mapa._segment_budget()  # noqa: SLF001

        mapa.resize(1200, 800)
        grande = mapa._segment_budget()  # noqa: SLF001

        assert grande > pequeno * 3, "o mapa grande tem de amostrar bem mais"

    def test_nunca_desce_abaixo_de_um_piso(self, app: QApplication) -> None:  # noqa: ARG002
        """Widget minúsculo não pode reduzir o traçado a quatro segmentos."""
        from gt7app.design.tokens import get_theme

        mapa = TrackMap(get_theme("dark"))
        mapa.resize(1, 1)

        assert mapa._segment_budget() >= 400  # noqa: SLF001

    def test_traçado_categorico_pinta_sem_estourar(self, app: QApplication) -> None:  # noqa: ARG002
        """Pintar de verdade: um `QPainter` mal fechado só aparece assim."""
        from PySide6.QtGui import QPixmap

        from gt7app.design.tokens import get_theme

        mapa = TrackMap(get_theme("dark"))
        mapa.resize(400, 300)
        pontos = [(float(i), float(i % 7)) for i in range(500)]
        mapa.set_paths(
            [
                TrackPath(
                    "t",
                    "#fff",
                    pontos,
                    colors=["#0f0" if i % 3 else "#f00" for i in range(500)],
                    distances=[float(i) for i in range(500)],
                )
            ]
        )
        mapa.set_legend([("#0f0", "acelerador"), ("#f00", "freio")])
        mapa.render(QPixmap(mapa.size()))


class TestInibidorDeSuspensao:
    def test_e_idempotente_nos_dois_sentidos(self) -> None:
        """O Qt emite mudança de estado com mais frequência do que se imagina:
        alternar de janela e voltar dispara vários eventos."""
        guarda = KeepAwake()
        try:
            guarda.acquire()
            guarda.acquire()
            guarda.release()
            guarda.release()
        finally:
            guarda.release()
        assert not guarda.is_active

    def test_soltar_sem_segurar_nao_estoura(self) -> None:
        KeepAwake().release()

    @pytest.mark.skipif(
        sys.platform not in ("darwin", "linux"),
        reason="depende do utilitário do sistema",
    )
    def test_segura_de_verdade_quando_a_ferramenta_existe(self) -> None:
        """Se o utilitário do sistema estiver instalado, o processo tem de
        subir. Sem isto, o teste passaria num ambiente onde nada funciona."""
        import shutil

        ferramenta = "caffeinate" if sys.platform == "darwin" else "systemd-inhibit"
        if shutil.which(ferramenta) is None:
            pytest.skip(f"{ferramenta} não instalado neste ambiente")

        guarda = KeepAwake()
        try:
            guarda.acquire()
            assert guarda.is_active
        finally:
            guarda.release()
        assert not guarda.is_active

    def test_a_janela_solta_ao_fechar(self, montado) -> None:  # noqa: ANN001
        """Um inibidor vazado deixaria a máquina acordada para sempre."""
        montado.close()
        assert not montado._keep_awake.is_active  # noqa: SLF001


class TestRecarregarPistasNaoPreenche:
    """A lista de pistas passou a ser recarregada toda vez que a aba abre.

    Isso conserta a defasagem (renomear no Histórico não aparecia aqui até
    reiniciar) e reabriu, por outra porta, o defeito que já custou uma sessão
    inteira gravada sob o nome errado: `clear()` + `addItem()` deixa o
    `currentIndex` em 0, e o campo se preenchia sozinho com a primeira pista em
    ordem alfabética. O teste prende os dois comportamentos juntos.
    """

    def test_recarregar_nao_preenche_campo_vazio(self, montado) -> None:  # noqa: ANN001
        live = montado._pages[0]  # noqa: SLF001
        assert live._track_input.currentText() == ""  # noqa: SLF001

        live._reload_tracks()  # noqa: SLF001

        assert live._track_input.currentText() == ""  # noqa: SLF001
        assert live._track_input.count() > 0, "a lista foi carregada mesmo assim"  # noqa: SLF001

    def test_recarregar_preserva_o_que_foi_escolhido(self, montado) -> None:  # noqa: ANN001
        live = montado._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("Interlagos")  # noqa: SLF001

        live._reload_tracks()  # noqa: SLF001

        assert live._track_input.currentText() == "Interlagos"  # noqa: SLF001

    def test_entrar_na_aba_traz_pista_renomeada(self, montado) -> None:  # noqa: ANN001
        """Era a defasagem: o nome novo só aparecia depois de reiniciar."""
        live = montado._pages[0]  # noqa: SLF001
        core = live.core
        alvo = next(t for t in core.tracks.get_all() if t.name == "Interlagos")
        assert alvo.id is not None
        core.tracks.rename(alvo.id, "Suzuka Circuit East Course")

        live.on_enter()

        nomes = [live._track_input.itemText(i) for i in range(live._track_input.count())]  # noqa: SLF001
        assert "Interlagos" not in nomes
