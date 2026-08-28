"""
Os canais novos da Análise e o cartão do cursor da Comparação.

O que se verifica aqui é a **ligação**: os módulos de guinada e de auxílios já
têm testes próprios, e os gráficos também. O que ninguém testa sozinho é se a
página liga uma coisa na outra — e é justamente aí que os defeitos desta fase
apareceram antes: uma série alimentada pelo canal errado, um cursor que move
metade dos gráficos, um rótulo escrito num `QLabel` que outra linha apaga três
instruções depois.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="a página é Qt")

from datetime import datetime  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7app.pages.analysis import CHART_BOOST, CHART_GRIP, CHART_YAW  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap, TelemetryPoint  # noqa: E402
from gt7core.telemetry.protocol import (  # noqa: E402
    FLAG_CAR_ON_TRACK,
    FLAG_TCS_ACTIVE,
)
from gt7core.telemetry.sources.mock import synthetic_lap  # noqa: E402
from tests.conftest import dispose_window  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _gravar(
    core,
    track_id: int,
    lap_time_ms: int,
    *,
    com_tcs: bool = False,
    flags: bool = True,
    car: str | None = None,
) -> int:
    """Grava uma volta sintética.

    `flags=False` simula uma volta **anterior à versão 7 do banco**, que é o
    único jeito de ficar sem o estado dos auxílios: uma volta gravada hoje
    sempre traz o bitfield, mesmo com nenhum auxílio atuando. Confundir os dois
    casos foi o primeiro palpite deste teste, e ele reprovou por isso.
    """
    for frame in synthetic_lap(lap_time_ms=lap_time_ms):
        core.engine.on_frame(frame)
    pontos = list(core.engine._buffer)  # noqa: SLF001

    for i, p in enumerate(pontos):
        campos = {f: getattr(p, f) for f in p.__slots__}
        if not flags:
            campos["flags"] = None
        elif com_tcs:
            campos["flags"] = FLAG_CAR_ON_TRACK | (
                FLAG_TCS_ACTIVE if 100 <= i <= 200 else 0
            )
        pontos[i] = type(p)(**campos)

    return core.laps.save(
        Lap(
            track_id=track_id,
            car_id=core.cars.get_or_create(car) if car else None,
            lap_time_ms=lap_time_ms,
            start_time=datetime.now(),
            points=pontos,
        )
    )


def _pontos(*, slip: float, speed: float = 120.0) -> list[TelemetryPoint]:
    """Pontos mínimos para exercitar o diagnóstico do canal de aderência.

    Só velocidade e escorregamento importam aqui: é o par que separa "volta
    gravada com o offset errado" de "roda legitimamente parada".
    """
    campos = dict.fromkeys(TelemetryPoint.__slots__, 0.0)
    campos.update(
        elapsed_ms=0, gear=0, flags=None, speed_kmh=speed,
        tire_slip_fl=slip, tire_slip_fr=slip,
        tire_slip_rl=slip, tire_slip_rr=slip,
    )
    return [TelemetryPoint(**campos) for _ in range(10)]


@pytest.fixture
def montado(app: QApplication, tmp_path):  # noqa: ANN001, ARG001
    """Núcleo + janela com duas voltas gravadas na mesma pista."""
    settings = Settings()
    settings.storage.database_path = tmp_path / "t.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"

    core = build_core(settings)
    try:
        track_id = core.tracks.get_or_create("Interlagos")
        _gravar(core, track_id, 92_000, com_tcs=True)
        core.engine.reset()
        _gravar(core, track_id, 93_000, com_tcs=True)

        window = build_gui(core)
        yield window, core
        dispose_window(window)
    finally:
        core.close()


class TestCanaisNovosDaAnalise:
    def test_guinada_e_aderencia_saem_preenchidos(self, montado) -> None:  # noqa: ANN001
        """Os dois canais novos precisam ter dado, não só existir.

        Um gráfico vazio e um gráfico que ninguém alimentou têm exatamente a
        mesma aparência na tela.
        """
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        assert not pagina._charts[CHART_YAW].is_empty  # noqa: SLF001
        assert not pagina._charts[CHART_GRIP].is_empty  # noqa: SLF001

    def test_aderencia_tem_uma_serie_por_roda(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        series = pagina._charts[CHART_GRIP]._series  # noqa: SLF001
        assert [s.label for s in series] == ["DE", "DD", "TE", "TD"]
        assert len({s.color for s in series}) == 4, "quatro rodas, quatro cores"

    def test_o_cursor_move_todos_os_canais_e_a_faixa(self, montado) -> None:  # noqa: ANN001
        """Um canal que não segue o cursor mostra outro ponto da pista que o
        vizinho — e as duas leituras lado a lado passam a se contradizer."""
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._on_hover(1200.0)  # noqa: SLF001

        for chart in pagina._charts:
            assert chart._cursor_m == 1200.0  # noqa: SLF001
        assert pagina._aid_band._cursor == 1200.0  # noqa: SLF001

    def test_a_temperatura_segue_o_cursor_e_volta_a_media(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        media = pagina._tyre_temps.temperatures  # noqa: SLF001
        assert media is not None
        assert pagina._tyre_caption.text() == "média da volta"  # noqa: SLF001

        pagina._on_hover(1200.0)  # noqa: SLF001
        assert "no cursor" in pagina._tyre_caption.text()  # noqa: SLF001

        pagina._on_hover_left()  # noqa: SLF001
        assert pagina._tyre_temps.temperatures == media  # noqa: SLF001
        assert pagina._tyre_caption.text() == "média da volta"  # noqa: SLF001

    def test_a_faixa_mostra_a_atuacao_do_tcs(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        assert pagina._aid_band._spans["TCS"], "o TCS foi gravado atuando"  # noqa: SLF001
        assert pagina._aid_band._note == ""  # noqa: SLF001

    def test_volta_sem_flags_diz_que_nao_foi_gravado(self, montado) -> None:  # noqa: ANN001
        """Faixa vazia afirmaria "nenhum auxílio atuou" sobre uma volta em que
        ninguém observou os auxílios. O texto é a única saída honesta."""
        window, core = montado
        track_id = core.tracks.get_or_create("Interlagos")
        core.engine.reset()
        antiga = _gravar(core, track_id, 94_000, flags=False)

        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._on_lap_selected(antiga)  # noqa: SLF001

        assert "não gravad" in pagina._aid_band._note  # noqa: SLF001
        assert pagina._aid_band._spans["TCS"] == []  # noqa: SLF001


class TestCursorDaComparacao:
    def test_mostra_as_duas_voltas_e_a_diferenca(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()

        pagina._on_hover(1200.0)  # noqa: SLF001

        ref, ana, dif = pagina._cursor_comparison._values["speed"]  # noqa: SLF001
        assert ref.text() != "—"
        assert ana.text() != "—"
        assert dif.text() not in ("—", "")

        # A diferença é comparada − referência, e a conta tem de fechar.
        assert float(dif.text()) == pytest.approx(
            float(ana.text()) - float(ref.text()), abs=0.15
        )

    def test_o_delta_vem_do_grafico_e_nao_de_outra_conta(self, montado) -> None:  # noqa: ANN001
        """Dois caminhos para o mesmo número acabam discordando em algum canto,
        e aí nada na tela diz qual dos dois está certo."""
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()
        pagina._on_hover(1200.0)  # noqa: SLF001

        do_grafico = pagina._delta_chart.value_at(1200.0)[0][1]  # noqa: SLF001
        assert f"{do_grafico:+.3f}" in pagina._cursor_delta.text()  # noqa: SLF001

    def test_limpar_apaga_o_cartao(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()
        pagina._on_hover(1200.0)  # noqa: SLF001

        pagina._clear("sem voltas")  # noqa: SLF001

        ref, _, _ = pagina._cursor_comparison._values["speed"]  # noqa: SLF001
        assert ref.text() == "—"
        assert pagina._cursor_delta.text() == "—"  # noqa: SLF001


class TestAvisoDeCanalImplausivel:
    """O gráfico que avisa quando pode estar errado.

    Um offset errado entrega um número que o gráfico desenha com toda a
    confiança do mundo. Foi esse silêncio que deixou toda volta de PS5 real ser
    gravada com distância 0,0 m sem ninguém notar.
    """

    def test_volta_normal_nao_avisa(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        assert pagina._grip_hint.text() == ""  # noqa: SLF001

    def test_canal_perto_de_zero_dispara_o_aviso(self, montado) -> None:  # noqa: ANN001
        """Quatro rodas paradas a volta inteira não é pilotagem, é canal errado."""
        from gt7app.widgets.charts import Series

        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._warn_if_slip_implausible(  # noqa: SLF001
            [Series("DE", "#fff", [(0.0, 2.0), (10.0, 3.0)])],
            _pontos(slip=1.0),
        )

        assert "implausível" in pagina._grip_hint.text()  # noqa: SLF001
        assert "não confiáveis" in pagina._grip_hint.text()  # noqa: SLF001

    def test_serie_vazia_nao_avisa(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina._warn_if_slip_implausible([], [])  # noqa: SLF001

        assert pagina._grip_hint.text() == ""  # noqa: SLF001

    def test_volta_antiga_diz_que_e_volta_antiga(self, montado) -> None:  # noqa: ANN001
        """Volta gravada antes da correção de offset pede coisa oposta.

        O aviso genérico manda procurar um defeito; aqui o defeito já foi
        corrigido e o dado é que está congelado no banco — o pacote bruto não é
        guardado. Mandar investigar o código seria mandar procurar o que não
        existe mais.
        """
        from gt7app.widgets.charts import Series

        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._warn_if_slip_implausible(  # noqa: SLF001
            [Series("DE", "#fff", [(0.0, 0.0), (10.0, 0.0)])],
            _pontos(slip=0.0),
        )

        texto = pagina._grip_hint.text()  # noqa: SLF001
        assert "antes da correção" in texto
        assert "Grave uma volta nova" in texto
        assert "implausível" not in texto

    def test_carro_parado_nao_conta_como_volta_antiga(self, montado) -> None:  # noqa: ANN001
        """Roda parada dá escorregamento zero **legitimamente**.

        Sem o piso de velocidade, o box e a largada — zero honesto — seriam lidos
        como o defeito de offset, e o aviso mandaria regravar uma volta que está
        inteira.
        """
        from gt7app.widgets.charts import Series

        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        pagina._warn_if_slip_implausible(  # noqa: SLF001
            [Series("DE", "#fff", [(0.0, 0.0), (10.0, 0.0)])],
            _pontos(slip=0.0, speed=0.0),
        )

        assert "implausível" in pagina._grip_hint.text()  # noqa: SLF001


class TestCanaisDaComparacao:
    """Os canais que a comparação ganhou: pedais, auxílios e turbo.

    O que importa prender aqui é a **correspondência de cor com volta**. Os
    mesmos dois traços aparecem agora em cinco gráficos, no mapa e na faixa de
    auxílios; se um deles trocar as cores, a mesma cor passa a significar voltas
    diferentes em quadros vizinhos — e nada na tela denuncia.
    """

    def test_acelerador_e_freio_tem_as_duas_voltas(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()

        for chart in (pagina._throttle_chart, pagina._brake_chart):  # noqa: SLF001
            assert not chart.is_empty
            assert [s.label for s in chart._series] == ["referência", "comparada"]  # noqa: SLF001

    def test_a_cor_significa_a_mesma_volta_em_todo_lugar(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()
        cores = pagina._colors  # noqa: SLF001

        for chart in (
            pagina._speed_chart,  # noqa: SLF001
            pagina._throttle_chart,  # noqa: SLF001
            pagina._brake_chart,  # noqa: SLF001
        ):
            referencia, comparada = chart._series  # noqa: SLF001
            assert referencia.color == cores.reference
            assert comparada.color == cores.compared

        mapa_ref, mapa_comp = pagina._map._paths  # noqa: SLF001
        assert mapa_ref.color == cores.reference
        assert mapa_comp.color == cores.compared

    def test_as_duas_cores_do_mapa_contrastam(self, montado) -> None:  # noqa: ANN001
        """Roxo e azul são vizinhos: sobrepostos, os dois traçados viravam uma
        linha só e a comparação de linha de corrida ficava ilegível."""
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        cores = pagina._colors  # noqa: SLF001

        def rgb(hexa: str) -> tuple[int, int, int]:
            limpo = hexa.lstrip("#")
            return tuple(int(limpo[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

        r1, g1, b1 = rgb(cores.reference)
        r2, g2, b2 = rgb(cores.compared)

        assert cores.reference != cores.compared
        # Separação em canal **azul**: é o eixo em que roxo e azul se pareciam.
        assert abs(b1 - b2) > 80, "as duas cores continuam vizinhas demais"

    def test_a_faixa_tem_uma_linha_por_auxilio_e_por_volta(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()

        assert set(pagina._aid_band._spans) == {  # noqa: SLF001
            "TCS ref.", "TCS comp.", "ASM ref.", "ASM comp.",
        }
        assert pagina._aid_band._spans["TCS ref."]  # noqa: SLF001
        assert pagina._aid_band._spans["TCS comp."]  # noqa: SLF001

    def test_a_faixa_colore_por_volta_e_nao_por_auxilio(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        cores = pagina._colors  # noqa: SLF001
        do_widget = pagina._aid_band._colors  # noqa: SLF001

        assert do_widget["TCS ref."] == do_widget["ASM ref."] == cores.reference
        assert do_widget["TCS comp."] == do_widget["ASM comp."] == cores.compared

    def test_o_abs_e_declarado_ausente_em_vez_de_omitido(self, montado) -> None:  # noqa: ANN001
        """Sem a frase, a ausência do ABS pareceria "o ABS não atuou"."""
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()

        assert "ABS" in pagina._aid_hint.text()  # noqa: SLF001


class TestTurboSomeQuandoNaoExiste:
    def test_com_turbo_o_grafico_aparece(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        for indice in (1, 2):
            pagina = window._pages[indice]  # noqa: SLF001
            pagina.refresh()

        analise = window._pages[1]  # noqa: SLF001
        assert not analise._charts[CHART_BOOST].isHidden()  # noqa: SLF001
        assert analise._boost_hint.text() == ""  # noqa: SLF001

        comparacao = window._pages[2]  # noqa: SLF001
        assert not comparacao._boost_chart.isHidden()  # noqa: SLF001

    def test_carro_aspirado_esconde_o_grafico_nas_duas_abas(self, montado) -> None:  # noqa: ANN001
        """Uma reta no zero ocupando 120 px é um gráfico que não responde nada.

        Some, e a linha de texto no lugar impede que a ausência vire "cadê o
        gráfico que estava aqui?".
        """
        window, core = montado
        track_id = core.tracks.get_or_create("Interlagos")

        core.engine.reset()
        for frame in synthetic_lap(lap_time_ms=95_000):
            core.engine.on_frame(frame)
        aspirados = []
        for p in core.engine._buffer:  # noqa: SLF001
            campos = {f: getattr(p, f) for f in p.__slots__}
            campos["turbo_boost"] = 1.0  # atmosférica: zero bar de sobrealimentação
            aspirados.append(type(p)(**campos))

        lap_id = core.laps.save(
            Lap(
                track_id=track_id,
                lap_time_ms=95_000,
                start_time=datetime.now(),
                points=aspirados,
            )
        )

        analise = window._pages[1]  # noqa: SLF001
        analise.refresh()
        analise._on_lap_selected(lap_id)  # noqa: SLF001

        assert analise._charts[CHART_BOOST].isHidden()  # noqa: SLF001
        assert "aspirado" in analise._boost_hint.text()  # noqa: SLF001

    def test_basta_uma_volta_ter_turbo_para_o_quadro_aparecer(self, montado) -> None:  # noqa: ANN001
        """Trocar de carro entre as voltas é legítimo, e aí a reta no zero de um
        deles é justamente a comparação."""
        window, _ = montado
        pagina = window._pages[2]  # noqa: SLF001
        pagina.refresh()

        for p in pagina._reference:  # noqa: SLF001
            object.__setattr__(p, "turbo_boost", 1.0)
        pagina._fill_boost()  # noqa: SLF001

        assert not pagina._boost_chart.isHidden(), "a comparada ainda tem turbo"  # noqa: SLF001


class TestAjustesDeLeitura:
    """As mudanças pedidas para a tela ler melhor.

    O que se prende aqui é o que some sem alarde: uma coluna a mais na tabela,
    um rótulo que para de ser atualizado, uma legenda que ninguém nota faltar.
    Nada disso quebra teste algum ao sumir — só a tela fica pior.
    """

    def test_esterco_sai_preenchido_e_separado_da_guinada(self, montado) -> None:  # noqa: ANN001
        """Dois canais, duas contas. Se o esterço copiasse a guinada, o gráfico
        novo seria o antigo com outro rótulo — e ninguém veria."""
        from gt7app.pages.analysis import CHART_STEER, CHART_YAW

        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        esterco = pagina._charts[CHART_STEER]._series[0].points  # noqa: SLF001
        guinada = pagina._charts[CHART_YAW]._series[0].points  # noqa: SLF001

        assert esterco
        assert [v for _, v in esterco] != [v for _, v in guinada]

    def test_tabela_de_curvas_sem_raio_e_sem_saida(self, montado) -> None:  # noqa: ANN001
        from gt7app.pages.analysis import CORNER_COLUMNS

        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        assert "Raio" not in CORNER_COLUMNS
        assert "Saída" not in CORNER_COLUMNS
        assert pagina._table.columnCount() == len(CORNER_COLUMNS)  # noqa: SLF001

    def test_mapa_da_analise_explica_as_bolinhas(self, montado) -> None:  # noqa: ANN001
        window, _ = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        rotulos = {r for _, r in pagina._map._marker_legend}  # noqa: SLF001
        assert rotulos == {"curva (ápice)", "setor"}

    def test_analise_mostra_o_carro_da_volta(self, montado) -> None:  # noqa: ANN001
        """Sem isto, duas voltas de carros diferentes na mesma pista são
        indistinguíveis no cabeçalho — e o delta entre elas parece pilotagem."""
        window, core = montado
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()

        assert pagina._selector._car_label.isVisibleTo(pagina._selector)  # noqa: SLF001
        assert pagina._selector._car_label.text()  # noqa: SLF001
        del core

    def test_ip_do_ps5_aparece_ao_vivo(self, montado) -> None:  # noqa: ANN001
        """O IP **configurado**, não um exemplo: o rótulo lê as Configurações."""
        window, core = montado
        pagina = window._pages[0]  # noqa: SLF001

        core.settings.telemetry.source = "udp"
        core.settings.telemetry.ps_ip = "192.168.15.156"
        pagina._refresh_ps_ip()  # noqa: SLF001

        assert "192.168.15.156" in pagina._ps_ip_label.text()  # noqa: SLF001

    def test_sem_ip_o_rotulo_diz_que_falta(self, montado) -> None:  # noqa: ANN001
        """Rótulo vazio se leria como "está tudo certo", que é o oposto."""
        window, core = montado
        pagina = window._pages[0]  # noqa: SLF001

        core.settings.telemetry.source = "udp"
        core.settings.telemetry.ps_ip = ""
        pagina._refresh_ps_ip()  # noqa: SLF001

        assert "sem IP" in pagina._ps_ip_label.text()  # noqa: SLF001

    def test_com_fonte_sintetica_nao_ha_ip_para_mostrar(self, montado) -> None:  # noqa: ANN001
        window, core = montado
        pagina = window._pages[0]  # noqa: SLF001

        core.settings.telemetry.source = "mock"
        pagina._refresh_ps_ip()  # noqa: SLF001

        assert pagina._ps_ip_label.text() == ""  # noqa: SLF001


class TestComparacaoEntrePistas:
    """Comparar voltas de pistas diferentes não significa nada.

    O alinhamento é por distância no mesmo traçado: 1.200 m em Interlagos e
    1.200 m em Suzuka são pedaços de asfalto sem relação, e o delta entre eles
    seria um número bem formatado sobre uma pergunta sem sentido.
    """

    def test_segunda_volta_so_oferece_voltas_da_pista_escolhida(self, montado) -> None:  # noqa: ANN001
        window, core = montado
        pagina = window._pages[2]  # noqa: SLF001

        outra = core.tracks.get_or_create("Suzuka")
        core.engine.reset()
        de_suzuka = _gravar(core, outra, 95_000)

        pagina.refresh()
        ofertadas = {
            pagina._analysed_selector._lap_combo.itemData(i)  # noqa: SLF001
            for i in range(pagina._analysed_selector._lap_combo.count())  # noqa: SLF001
        }

        assert de_suzuka not in ofertadas, "volta de outra pista entrou na lista"

    def test_carros_diferentes_avisam(self, montado) -> None:  # noqa: ANN001
        """Metade do delta pode ser do carro, e nada na tela dizia isso."""
        window, core = montado
        pagina = window._pages[2]  # noqa: SLF001
        pista = core.tracks.get_or_create("Interlagos")

        core.engine.reset()
        gt3 = _gravar(core, pista, 91_000, car="Porsche 911 GT3")
        core.engine.reset()
        civic = _gravar(core, pista, 96_000, car="Honda Civic Type R")

        pagina._show_cars(gt3, civic)  # noqa: SLF001

        assert "Carros diferentes" in pagina._car_hint.text()  # noqa: SLF001
        assert "Porsche 911 GT3" in pagina._car_hint.text()  # noqa: SLF001
        assert "Honda Civic Type R" in pagina._car_hint.text()  # noqa: SLF001

    def test_mesmo_carro_nao_avisa(self, montado) -> None:  # noqa: ANN001
        """Aviso que aparece sempre deixa de ser lido."""
        window, core = montado
        pagina = window._pages[2]  # noqa: SLF001
        pista = core.tracks.get_or_create("Interlagos")

        core.engine.reset()
        uma = _gravar(core, pista, 91_000, car="Porsche 911 GT3")
        core.engine.reset()
        outra = _gravar(core, pista, 92_500, car="Porsche 911 GT3")

        pagina._show_cars(uma, outra)  # noqa: SLF001

        assert pagina._car_hint.text() == ""  # noqa: SLF001
        assert "Porsche" in pagina._reference_selector._car_label.text()  # noqa: SLF001
