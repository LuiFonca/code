"""
Fumaça da casca: navegação, páginas e paleta de comandos.

Estes testes exigem Qt e um `QApplication`; rodam com `QT_QPA_PLATFORM=offscreen`
e são pulados onde o PySide6 não está instalado — o núcleo continua headless.

O que se verifica aqui é o que só aparece montando de verdade: que cada página
sobe com dados reais no banco, que trocar de página não estoura, e que a paleta
encontra e executa comandos. Um `AttributeError` numa página que ninguém abriu
durante o desenvolvimento é exatamente o defeito que este arquivo pega.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from gt7core.config.settings import Settings, StorageConfig, TelemetryConfig

HAS_QT = importlib.util.find_spec("PySide6") is not None

pytestmark = pytest.mark.skipif(
    not HAS_QT, reason="PySide6 não instalado — o núcleo é headless"
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        telemetry=TelemetryConfig(source="mock"),
        storage=StorageConfig(
            database_path=tmp_path / "shell.db",
            telemetry_path=tmp_path / "telemetry",
        ),
    )


@pytest.fixture(scope="module")
def qt_app():  # noqa: ANN201  (tipo Qt)
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def shell(tmp_path: Path, qt_app):  # noqa: ANN001, ANN201
    """Uma casca montada sobre um banco com voltas de verdade."""
    from gt7app.application import build_core, build_gui
    from gt7core.domain.models import Track
    from gt7core.telemetry.sources.mock import synthetic_session

    core = build_core(make_settings(tmp_path))

    track_id = core.tracks.get_or_create("Pista de Teste")
    core.session_manager.set_track(Track(id=track_id, name="Pista de Teste"))
    core.session_manager.start_session()
    for frame in synthetic_session(lap_count=4):
        core.engine.on_frame(frame)
    core.session_manager.end_session()

    window = build_gui(core)
    window.show()
    qt_app.processEvents()

    yield window, core, qt_app

    window.close()


class TestNavegacao:
    def test_todas_as_paginas_montam_e_carregam(self, shell) -> None:  # noqa: ANN001
        """Percorre as seis páginas com dados reais.

        É o teste que pega o erro que só existe na página que ninguém abriu.
        """
        window, _core, app = shell

        assert len(window._pages) == 6  # noqa: SLF001
        for index, page in enumerate(window._pages):  # noqa: SLF001
            window._activate(index)  # noqa: SLF001
            app.processEvents()
            assert window._stack.currentIndex() == index  # noqa: SLF001
            assert page.isVisible()

    def test_paginas_tem_identificadores_unicos(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        ids = [page.page_id for page in window._pages]  # noqa: SLF001
        assert len(set(ids)) == len(ids)
        assert "" not in ids

    def test_indice_invalido_nao_muda_de_pagina(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        window._activate(2)  # noqa: SLF001
        window._activate(99)  # noqa: SLF001
        assert window._stack.currentIndex() == 2  # noqa: SLF001

    def test_botao_da_navegacao_acompanha_a_pagina(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(3)  # noqa: SLF001
        app.processEvents()
        button = window._nav_group.button(3)  # noqa: SLF001
        assert button is not None and button.isChecked()


class TestPaginasComDados:
    def test_analise_detecta_curvas_da_volta_selecionada(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        analysis = window._pages[1]  # noqa: SLF001
        # A volta sintética tem quatro mínimos de velocidade — é a mesma
        # verdade conhecida usada nos testes da Fase 4.
        assert len(analysis._corners) == 4  # noqa: SLF001
        assert analysis._table.rowCount() == 4  # noqa: SLF001

    def test_comparacao_preenche_a_tabela_de_perda(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(2)  # noqa: SLF001
        app.processEvents()

        compare = window._pages[2]  # noqa: SLF001
        assert compare._table.rowCount() > 0, "nenhum trecho comparado"  # noqa: SLF001

    def test_historico_lista_as_voltas_gravadas(self, shell) -> None:  # noqa: ANN001
        window, core, app = shell
        window._activate(3)  # noqa: SLF001
        app.processEvents()

        history = window._pages[3]  # noqa: SLF001
        tracks = core.tracks.get_all()
        assert tracks
        expected = len(core.laps.get_by_track(tracks[0].id))
        assert history._table.rowCount() == expected  # noqa: SLF001

    def test_perfil_do_piloto_monta(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(4)  # noqa: SLF001
        app.processEvents()

        driver = window._pages[4]  # noqa: SLF001
        assert driver._summary.cards["laps"]._value.text() not in ("", "—")  # noqa: SLF001

    def test_recarregar_duas_vezes_nao_duplica_conteudo(self, shell) -> None:  # noqa: ANN001
        """O bug do cartão: limpar mal levava o título para o rodapé."""
        window, _core, app = shell
        window._activate(4)  # noqa: SLF001
        app.processEvents()

        driver = window._pages[4]  # noqa: SLF001
        first = driver._strengths.body().count()  # noqa: SLF001
        driver.refresh()
        app.processEvents()
        assert driver._strengths.body().count() == first  # noqa: SLF001


class TestPaletaDeComandos:
    def test_abre_lista_e_fecha(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell

        window._palette.open()  # noqa: SLF001
        app.processEvents()
        assert window._palette.isVisible()  # noqa: SLF001
        assert window._palette._list.count() > 0  # noqa: SLF001

        window._palette.close_palette()  # noqa: SLF001
        app.processEvents()
        assert not window._palette.isVisible()  # noqa: SLF001

    def test_busca_filtra_e_executa(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell

        window._activate(0)  # noqa: SLF001
        window._palette.open()  # noqa: SLF001
        window._palette._input.setText("comparar")  # noqa: SLF001
        app.processEvents()

        assert window._palette._list.count() > 0  # noqa: SLF001
        window._palette._run_current()  # noqa: SLF001
        app.processEvents()

        # Executar "Ir para Comparar" navegou de verdade e fechou a paleta.
        assert window._pages[window._stack.currentIndex()].page_id == "compare"  # noqa: SLF001
        assert not window._palette.isVisible()  # noqa: SLF001

    def test_comandos_de_navegacao_cobrem_todas_as_paginas(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        registered = {c.id for c in window._commands.all()}  # noqa: SLF001
        for page in window._pages:  # noqa: SLF001
            assert f"go.{page.page_id}" in registered


class TestEngenheiroNaTela:
    """Fase 8: o conselho existe na interface, não só no plugin."""

    def test_a_comparacao_tem_cartao_do_engenheiro(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(2)  # noqa: SLF001
        app.processEvents()
        assert window._pages[2]._advice is not None  # noqa: SLF001

    def test_o_perfil_tem_cartao_de_relatorio(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(4)  # noqa: SLF001
        app.processEvents()
        assert window._pages[4]._advice is not None  # noqa: SLF001

    def test_o_servico_existe_e_e_qt(self, shell) -> None:  # noqa: ANN001
        """`build_core` monta o engenheiro; `build_gui` monta a ponte Qt."""
        from PySide6.QtCore import QObject

        _window, core, _app = shell
        assert isinstance(core.engineer_service, QObject)
        assert core.engineer_service.is_available == (core.engineer is not None)

    def test_o_comando_do_engenheiro_leva_a_comparacao(self, shell) -> None:  # noqa: ANN001
        window, core, app = shell
        if not core.engineer_service.is_available:
            pytest.skip("gt7ai não instalado")

        window._activate(0)  # noqa: SLF001
        command = window._commands.get("engineer.debrief")  # noqa: SLF001
        assert command is not None
        command.run()
        app.processEvents()
        assert window._pages[window._stack.currentIndex()].page_id == "compare"  # noqa: SLF001

    def test_a_volta_reseta_a_cota_do_radio(self, tmp_path: Path, qt_app) -> None:  # noqa: ANN001
        """Sem alguém chamando `new_lap`, o rádio emudece após as primeiras notas."""
        from gt7app.application import build_core
        from gt7core.domain.models import Track
        from gt7core.telemetry.sources.mock import synthetic_session

        core = build_core(make_settings(tmp_path))
        if core.engineer is None:
            pytest.skip("gt7ai não instalado")

        track_id = core.tracks.get_or_create("Pista")
        core.session_manager.set_track(Track(id=track_id, name="Pista"))
        core.session_manager.start_session()

        seen: list[int] = []
        original = core.engineer.new_lap
        core.engineer.new_lap = lambda: (seen.append(1), original())  # type: ignore[method-assign]

        for frame in synthetic_session(lap_count=3):
            core.engine.on_frame(frame)
        core.session_manager.end_session()
        core.close()

        assert seen, "ninguém virou a página da cota de notas"


class TestRadio:
    """Fase 9: o evento detectado no núcleo vira nota na tela ao vivo."""

    def test_a_pagina_ao_vivo_tem_radio(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(0)  # noqa: SLF001
        app.processEvents()
        assert window._pages[0]._radio is not None  # noqa: SLF001

    def test_o_evento_do_nucleo_chega_a_pagina(self, shell) -> None:  # noqa: ANN001
        """A travessia inteira: detector → barramento → adaptador Qt → página."""
        from gt7core.analytics.live import RaceEvent, RaceEventDetected

        window, core, app = shell
        window._activate(0)  # noqa: SLF001
        app.processEvents()

        page = window._pages[0]  # noqa: SLF001
        recebidos: list[object] = []
        original = page._on_race_event  # noqa: SLF001
        page._on_race_event = lambda e: (recebidos.append(e), original(e))  # noqa: SLF001
        window._vm.adapter.subscribe(  # noqa: SLF001
            RaceEventDetected, page._on_race_event  # noqa: SLF001
        )

        core.bus.publish(
            RaceEventDetected(
                event=RaceEvent(
                    kind="travamento", distance_m=900.0, elapsed_ms=12_000,
                    detail="travamento na dianteira por 300 ms",
                )
            )
        )
        app.processEvents()
        assert recebidos, "o evento não atravessou até a página"

    def test_o_detector_esta_ligado_ao_barramento(self, shell) -> None:  # noqa: ANN001
        """Uma sessão sintética inteira produz eventos de verdade."""
        from gt7core.analytics.live import RaceEventDetected
        from gt7core.telemetry.sources.mock import synthetic_lap

        _window, core, _app = shell
        vistos: list[object] = []
        core.bus.subscribe(RaceEventDetected, vistos.append)

        core.live_detector.reset()
        for frame in synthetic_lap(lap_time_ms=102_000):
            core.engine.on_frame(frame)

        assert vistos, "nenhum evento ao vivo saiu de uma volta inteira"


class TestMapaDePista:
    """Fase 6: mapa de calor, cursor sincronizado e interação."""

    def test_mapa_recebe_mapa_de_calor_e_distancias(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        track_map = window._pages[1]._map  # noqa: SLF001
        paths = track_map._paths  # noqa: SLF001
        assert paths, "o mapa não recebeu traçado"
        assert paths[0].has_heatmap, "sem valores, não há mapa de calor"
        assert paths[0].is_locatable, "sem distâncias, o cursor não funciona"

    def test_cursor_do_grafico_alcanca_o_mapa(self, shell) -> None:  # noqa: ANN001
        """Os gráficos dizem *o que*; o mapa diz *onde*. Um cursor só."""
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        page = window._pages[1]  # noqa: SLF001
        target = page._corners[2].apex_distance_m  # noqa: SLF001
        page._on_hover(target)  # noqa: SLF001
        app.processEvents()

        assert page._map._cursor_m == target  # noqa: SLF001
        for chart in page._charts:  # noqa: SLF001
            assert chart._cursor_m == target  # noqa: SLF001

        page._on_hover_left()  # noqa: SLF001
        assert page._map._cursor_m is None  # noqa: SLF001

    def test_localiza_o_ponto_pela_distancia(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        path = window._pages[1]._map._paths[0]  # noqa: SLF001
        target = path.distances[len(path.distances) // 3]
        index = path.index_at_distance(target)
        assert index is not None
        assert abs(path.distances[index] - target) < 1.0

        # Fora das pontas satura em vez de estourar.
        assert path.index_at_distance(-100.0) == 0
        assert path.index_at_distance(1e9) == len(path.points) - 1

    def test_setores_e_apices_aparecem_como_marcadores(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        labels = {m.label for m in window._pages[1]._map._markers}  # noqa: SLF001
        assert {"C1", "C2", "C3", "C4"} <= labels, "ápices faltando no mapa"
        # Três setores produzem dois limites internos.
        assert {"S1", "S2"} <= labels, "limites de setor faltando"

    def test_comparacao_marca_os_piores_trechos_no_mapa(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(2)  # noqa: SLF001
        app.processEvents()

        page = window._pages[2]  # noqa: SLF001
        assert page._map._markers, "os piores trechos não foram marcados"  # noqa: SLF001
        assert len(page._map._markers) <= 3  # noqa: SLF001

    def test_cursor_unico_na_comparacao(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(2)  # noqa: SLF001
        app.processEvents()

        page = window._pages[2]  # noqa: SLF001
        page._on_hover(1500.0)  # noqa: SLF001
        assert page._delta_chart._cursor_m == 1500.0  # noqa: SLF001
        assert page._speed_chart._cursor_m == 1500.0  # noqa: SLF001
        assert page._map._cursor_m == 1500.0  # noqa: SLF001

    def test_clique_longe_do_tracado_nao_move_o_cursor(self, shell) -> None:  # noqa: ANN001
        """Clicar no canto vazio não deve saltar para o outro lado da pista."""
        from PySide6.QtCore import QPointF

        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        track_map = window._pages[1]._map  # noqa: SLF001
        assert track_map._distance_at_pixel(QPointF(-500.0, -500.0)) is None  # noqa: SLF001


class TestSeloDeDadosSinteticos:
    """O defeito que fazia o gerador passar por telemetria real.

    Relato que originou isto: *"o aplicativo tá funcionando cheio de dados
    mocados"*. A pessoa abriu o programa, viu velocidade, RPM e marcha se
    mexendo, e concluiu que estava conectada ao console.

    A fonte sintética precisa existir — é o que permite conhecer o programa sem
    PS5. O defeito não é ela, é ela ser **indistinguível** da real, e o dano
    cresce com a qualidade do gerador: quanto mais convincente, mais tempo se
    perde antes de desconfiar.
    """

    def test_o_selo_aparece_na_fonte_sintetica(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        window._activate(0)  # noqa: SLF001
        app.processEvents()

        live = window._pages[0]  # noqa: SLF001
        assert live._synthetic_badge.isVisible()  # noqa: SLF001
        assert "SINTÉTICOS" in live._synthetic_badge.text()  # noqa: SLF001

    def test_o_selo_some_quando_a_fonte_vira_o_ps5(self, shell) -> None:  # noqa: ANN001
        """Um selo decidido só na construção mentiria na direção oposta:
        continuaria dizendo "sintético" com o console já conectado."""
        window, core, app = shell
        core.settings.telemetry.source = "udp"

        window._activate(1)  # noqa: SLF001
        app.processEvents()
        window._activate(0)  # noqa: SLF001
        app.processEvents()

        assert not window._pages[0]._synthetic_badge.isVisible()  # noqa: SLF001


class TestSeloAcompanhaATrocaDeFonte:
    """O selo mentindo é pior que o selo ausente.

    Relato: *"a aba ao vivo não tá pegando os dados da rede, ainda funciona com
    os dados mocados"*. A fonte trocava corretamente — verificado: o gerador
    para, some da lista de threads e não entrega mais um quadro sequer. O que
    continuava dizendo "sintético" era o selo, reavaliado só na **entrada** da
    página. Quem já estava em "Ao vivo", salvava Configurações e voltava,
    recebia a garantia visual de que os dados eram falsos quando não eram — e
    passa a duvidar de telemetria correta.
    """

    def test_conectar_reavalia_o_selo(self, shell) -> None:  # noqa: ANN001
        window, core, app = shell
        live = window._pages[0]  # noqa: SLF001
        window._activate(0)  # noqa: SLF001
        app.processEvents()
        assert live._synthetic_badge.isVisible()  # noqa: SLF001

        # Configurações muda a fonte sem a página ser reentrada.
        core.settings.telemetry.source = "udp"
        live._update_synthetic_badge()  # noqa: SLF001
        app.processEvents()

        assert not live._synthetic_badge.isVisible(), (
            "o selo continuou afirmando que a telemetria real é sintética"
        )


class TestPausaCongelaOsNumeros:
    """Pausar o jogo não pode apagar o que se quer justamente olhar.

    A escolha anterior era apagar, para distinguir "carro parado" de
    "transmissão perdida". Mas o caso comum é o jogo pausado, e ali apagar
    destrói os valores do instante em que se pausou. A barra de status assume a
    tarefa de avisar — que era o único motivo de apagar.
    """

    def test_os_valores_permanecem(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        live = window._pages[0]  # noqa: SLF001
        window._activate(0)  # noqa: SLF001
        app.processEvents()

        live._grid.cards["speed"].set_value("187")  # noqa: SLF001
        live._on_stale()  # noqa: SLF001
        app.processEvents()

        assert live._grid.cards["speed"]._value.text() == "187"  # noqa: SLF001

    def test_a_barra_avisa_que_nao_e_tempo_real(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._on_stale()  # noqa: SLF001
        assert "congelados" in live._status.text().lower()


class TestEixoPorTempo:
    def test_o_seletor_troca_a_unidade_dos_graficos(self, shell) -> None:  # noqa: ANN001
        window, _core, app = shell
        live = window._pages[0]  # noqa: SLF001
        window._activate(0)  # noqa: SLF001
        app.processEvents()

        live._x_selector.setCurrentIndex(1)  # noqa: SLF001
        app.processEvents()

        assert live._x_mode == "time"  # noqa: SLF001
        assert live._speed_chart._x_unit == "s"  # noqa: SLF001
        assert live._pedals_chart._x_unit == "s"  # noqa: SLF001

        live._x_selector.setCurrentIndex(0)  # noqa: SLF001
        assert live._speed_chart._x_unit == "m"  # noqa: SLF001

    def test_na_analise_o_cursor_encontra_a_amostra_certa(self, shell) -> None:  # noqa: ANN001
        """No modo tempo o cursor recebe segundos.

        Sem tradução, `_point_at` procuraria "a amostra a 42 metros" recebendo
        42 **segundos** e apontaria para o começo da volta com o mouse no fim.
        """
        window, _core, app = shell
        window._activate(1)  # noqa: SLF001
        app.processEvents()

        page = window._pages[1]  # noqa: SLF001
        assert page._points, "a fixture precisa de uma volta carregada"  # noqa: SLF001

        alvo = page._points[len(page._points) // 2]  # noqa: SLF001
        page._x_selector.setCurrentIndex(1)  # noqa: SLF001
        app.processEvents()

        encontrado = page._point_at_x(alvo.elapsed_ms / 1000.0)  # noqa: SLF001
        assert encontrado is not None
        assert encontrado.distance_m == pytest.approx(alvo.distance_m, abs=1.0)


class TestPistaNaoSeAutoPreenche:
    """O defeito que impedia a detecção automática de funcionar.

    Um combo carregado com 105 pistas seleciona a primeira em ordem alfabética
    sozinho. Conectar sem tocar no campo gravava a sessão como "24 Heures du
    Mans" — nome que ninguém digitou e que parece escolhido. E, com a pista já
    definida, a detecção pelo comprimento nunca rodava, porque ela só age quando
    não há pista.
    """

    def test_o_campo_comeca_vazio(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        assert live._track_input.currentText() == ""  # noqa: SLF001
        assert live._resolve_track_name() == ""  # noqa: SLF001

    def test_um_ip_digitado_e_recusado(self, shell) -> None:  # noqa: ANN001
        """Uma sessão inteira já foi gravada sob "192.168.15.156".

        O campo de conexão mora em Configurações; aqui é o nome do circuito. Sem
        a guarda, o histórico agrupa voltas por um rótulo que não é pista.
        """
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("192.168.15.156")  # noqa: SLF001

        assert live._resolve_track_name() == ""  # noqa: SLF001
        assert "endereço de rede" in live._status.text()  # noqa: SLF001

    def test_um_nome_de_pista_passa(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("Interlagos")  # noqa: SLF001
        assert live._resolve_track_name() == "Interlagos"  # noqa: SLF001

    def test_candidato_unico_e_aplicado(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("")  # noqa: SLF001

        live._on_track_candidates(["Suzuka Circuit"])  # noqa: SLF001
        assert live._track_input.currentText() == "Suzuka Circuit"  # noqa: SLF001

    def test_ambiguidade_sugere_em_vez_de_escolher(self, shell) -> None:  # noqa: ANN001
        """Vários circuitos compartilham comprimento. Escolher sozinho entre
        eles é errar em silêncio — o histórico passa a misturar pistas."""
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("")  # noqa: SLF001

        live._on_track_candidates(["Goodwood", "Brands Hatch", "Watkins Glen"])  # noqa: SLF001

        assert live._track_input.currentText() == "", "não pode decidir sozinho"  # noqa: SLF001
        assert "provável" in live._status.text()  # noqa: SLF001
        assert "Goodwood" in live._status.text()  # noqa: SLF001

    def test_pista_ja_escolhida_nao_e_sobrescrita(self, shell) -> None:  # noqa: ANN001
        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001
        live._track_input.setCurrentText("Minha Pista")  # noqa: SLF001

        live._on_track_candidates(["Suzuka Circuit"])  # noqa: SLF001
        assert live._track_input.currentText() == "Minha Pista"  # noqa: SLF001


class TestJanelaDeTrintaSegundos:
    def test_a_virada_de_volta_nao_estica_o_eixo(self, shell) -> None:  # noqa: ANN001
        """O tempo decorrido **zera** a cada volta.

        Um rastro que atravessa a virada tem tempos indo de 102 s a 0 s, e o
        eixo passava a medir 103 s de janela — medido, não suposto.
        """
        from gt7app.pages.live import TRAIL_WINDOW_S

        window, _core, _app = shell
        live = window._pages[0]  # noqa: SLF001

        live._trail = [  # noqa: SLF001
            (3700.0, 100.0, 180.0, 90.0, 0.0),
            (3799.0, 102.0, 150.0, 0.0, 80.0),
        ]

        class _Ponto:
            distance_m = 5.0
            elapsed_ms = 100
            speed_kmh = 60.0
            rpm = 3000.0
            gear = 2
            throttle = 50.0
            brake = 0.0

        class _Quadro:
            lap_count = 5
            tire_temp_fl = tire_temp_fr = 80.0
            tire_temp_rl = tire_temp_rr = 80.0

        class _Evento:
            point = _Ponto()
            frame = _Quadro()

        live._on_frame(_Evento())  # noqa: SLF001

        tempos = [row[1] for row in live._trail]  # noqa: SLF001
        assert max(tempos) - min(tempos) <= TRAIL_WINDOW_S
