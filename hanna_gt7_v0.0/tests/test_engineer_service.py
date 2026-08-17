"""
Fase 8 — a fronteira assíncrona e o gráfico que a expôs.

Dois grupos de teste, com origens diferentes.

O primeiro verifica a mitigação do **R2** da auditoria: o engenheiro roda fora
da thread da interface, o resultado volta nela, e resposta que deixou de ser
relevante não aparece na tela. São propriedades de concorrência, então cada uma
é verificada com um relógio ou um identificador de thread — não com "pareceu
funcionar".

O segundo existe por causa de um defeito que só apareceu ao **medir**. A promessa
da fase é "a interface não congela", e a primeira medição mostrou 938 ms de
bloqueio. Não era a IA: era `DistanceChart._to_pixel` chamando `_y_bounds()` uma
vez por ponto, o que varre todas as séries inteiras a cada chamada. Com ~6000
amostras por volta, repintar era quadrático. O teste aqui fixa a propriedade que
não pode voltar a se perder.
"""

from __future__ import annotations

import importlib.util
import threading
import time

import pytest

HAS_QT = importlib.util.find_spec("PySide6") is not None

pytestmark = pytest.mark.skipif(
    not HAS_QT, reason="PySide6 não instalado — o núcleo é headless"
)


@pytest.fixture(scope="module")
def qt_app():  # noqa: ANN201
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def pump(app, service, *, timeout: float = 5.0) -> None:  # noqa: ANN001
    """Roda o laço de eventos até o serviço ficar ocioso.

    Não é `sleep`: um teste que dorme um tempo fixo passa por sorte na máquina
    rápida e falha na lenta.
    """
    deadline = time.monotonic() + timeout
    while service.is_busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


class FakeEngineer:
    """Engenheiro roteirizado, com a thread de execução registrada."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []
        self.threads: list[int] = []
        self.laps = 0
        self.sessions = 0

    def _work(self, kind: str, result: object) -> object:
        self.calls.append(kind)
        self.threads.append(threading.get_ident())
        if self.delay:
            time.sleep(self.delay)
        return result

    def debrief(self, report, **kwargs):  # noqa: ANN001, ANN201
        return self._work("debrief", _advice("debrief", kwargs.get("track", "")))

    def session_report(self, profile, **kwargs):  # noqa: ANN001, ANN201
        return self._work("session", _advice("session", kwargs.get("track", "")))

    def quick_note(self, situation, **kwargs):  # noqa: ANN001, ANN201
        return self._work("quick", _advice("quick", situation))

    def new_lap(self) -> None:
        self.laps += 1

    def new_session(self) -> None:
        self.sessions += 1


def _advice(level: str, marker: str):  # noqa: ANN202
    from gt7ai import Advice, AdviceLevel

    return Advice(level=AdviceLevel(level), headline=marker)


class TestForaDaThreadDaInterface:
    """O R2 da auditoria: a UI não pode parar esperando o modelo."""

    def test_o_trabalho_roda_em_outra_thread(self, qt_app) -> None:  # noqa: ANN001
        from gt7app.services.engineer import EngineerService

        engineer = FakeEngineer(delay=0.05)
        service = EngineerService(engineer)
        service.request_debrief(None, track="Suzuka")
        pump(qt_app, service)

        assert engineer.threads, "o engenheiro não foi chamado"
        assert engineer.threads[0] != threading.get_ident(), (
            "o engenheiro rodou na thread da interface — é exatamente o R2"
        )

    def test_o_resultado_chega_na_thread_da_interface(self, qt_app) -> None:  # noqa: ANN001
        """Tocar widget de outra thread corrompe a interface e derruba o Qt."""
        from gt7app.services.engineer import EngineerService

        received: list[int] = []
        service = EngineerService(FakeEngineer())
        service.ready.connect(lambda _a: received.append(threading.get_ident()))

        service.request_debrief(None, track="Suzuka")
        pump(qt_app, service)

        assert received == [threading.get_ident()]

    def test_a_interface_continua_respondendo_durante_a_chamada(self, qt_app) -> None:  # noqa: ANN001
        """Mede o bloqueio real do laço de eventos enquanto o modelo pensa."""
        from gt7app.services.engineer import EngineerService

        service = EngineerService(FakeEngineer(delay=0.4))
        service.request_debrief(None, track="Suzuka")

        worst = 0.0
        deadline = time.monotonic() + 2.0
        while service.is_busy and time.monotonic() < deadline:
            tick = time.monotonic()
            qt_app.processEvents()
            worst = max(worst, time.monotonic() - tick)
            time.sleep(0.005)

        assert worst < 0.1, f"laço de eventos bloqueado por {worst * 1000:.0f} ms"

    def test_avisa_que_comecou_a_pensar(self, qt_app) -> None:  # noqa: ANN001
        """Cartão parado por dez segundos é indistinguível de cartão quebrado."""
        from gt7app.services.engineer import EngineerService

        levels: list[str] = []
        service = EngineerService(FakeEngineer())
        service.started.connect(levels.append)

        service.request_debrief(None, track="X")
        pump(qt_app, service)
        service.request_session_report(None, track="X")
        pump(qt_app, service)

        assert levels == ["debrief", "session"]


class TestResultadoObsoleto:
    def test_resposta_de_pedido_cancelado_nao_aparece(self, qt_app) -> None:  # noqa: ANN001
        """O piloto pede a volta 5, troca para a 3, e a 5 responde depois.

        Sem o contador de geração, a tela mostraria a análise da volta errada —
        e nada no texto denunciaria isso.
        """
        from gt7app.services.engineer import EngineerService

        seen: list[str] = []
        service = EngineerService(FakeEngineer(delay=0.2))
        service.ready.connect(lambda a: seen.append(a.headline))

        service.request_debrief(None, track="volta 5")
        service.cancel_pending()
        pump(qt_app, service)

        assert seen == [], "conselho obsoleto chegou à tela"

    def test_pedido_novo_substitui_o_pendente(self, qt_app) -> None:  # noqa: ANN001
        """Trocar de volta três vezes não deve produzir três inferências.

        Numa máquina de 8 GB isso importa duplamente: cada chamada ocupa a
        memória que o modelo já quase esgota.
        """
        from gt7app.services.engineer import EngineerService

        engineer = FakeEngineer(delay=0.15)
        seen: list[str] = []
        service = EngineerService(engineer)
        service.ready.connect(lambda a: seen.append(a.headline))

        service.request_debrief(None, track="primeira")
        service.request_debrief(None, track="segunda")
        service.request_debrief(None, track="terceira")
        pump(qt_app, service)

        assert engineer.calls == ["debrief", "debrief"], (
            "a fila deveria ter no máximo o corrente e o último"
        )
        assert seen[-1] == "terceira", "o pedido mais recente não foi o exibido"

    def test_uma_chamada_por_vez(self, qt_app) -> None:  # noqa: ANN001
        from gt7app.services.engineer import EngineerService

        service = EngineerService(FakeEngineer(delay=0.1))
        service.request_debrief(None, track="A")
        assert service.is_busy
        service.request_debrief(None, track="B")
        # A segunda ficou pendente, não em execução paralela.
        pump(qt_app, service)
        assert not service.is_busy


class TestFimDePedidoSemConselho:
    """Três defeitos que só apareceram ao olhar a tela ao vivo renderizada."""

    def test_fim_e_sinalizado_mesmo_sem_conselho(self, qt_app) -> None:  # noqa: ANN001
        """O nível 1 devolve `None` quando não há nada a dizer.

        Sem um sinal de término, a tela ficava presa em "pensando" para sempre —
        indistinguível de um programa travado.
        """
        from gt7app.services.engineer import EngineerService

        class Mudo(FakeEngineer):
            def quick_note(self, situation, **kwargs):  # noqa: ANN001, ANN201
                return self._work("quick", None)

        fins: list[str] = []
        prontos: list[object] = []
        service = EngineerService(Mudo())
        service.finished.connect(fins.append)
        service.ready.connect(prontos.append)

        service.request_quick_note("contexto")
        pump(qt_app, service)

        assert prontos == [], "não havia conselho a entregar"
        assert fins == ["quick"], "o fim do pedido não foi sinalizado"

    def test_o_radio_volta_ao_silencio_e_nao_fica_pensando(self, qt_app) -> None:  # noqa: ANN001
        from gt7app.design.tokens import DARK_THEME
        from gt7app.widgets.radio import IDLE_TEXT, THINKING_TEXT, RadioCard

        radio = RadioCard(DARK_THEME)
        radio.show_thinking()
        assert radio._text.text() == THINKING_TEXT  # noqa: SLF001
        radio.show_idle()
        assert radio._text.text() == IDLE_TEXT  # noqa: SLF001

    def test_pedido_suprimido_nao_apaga_a_nota_boa(self, qt_app) -> None:  # noqa: ANN001
        """O defeito visível: o conselho aparecia e sumia sozinho.

        Um evento dispara o pedido, dez chegam atrás e viram um pendente. Quando
        o pendente roda, a cadência costuma recusá-lo — e esse pedido natimorto
        trocava a nota recém-entregue por "…" e depois por silêncio.
        """
        from gt7ai import Advice, AdviceLevel
        from gt7app.design.tokens import DARK_THEME
        from gt7app.widgets.radio import THINKING_TEXT, RadioCard

        radio = RadioCard(DARK_THEME)
        radio.show_advice(
            Advice(level=AdviceLevel.QUICK, headline="Freie mais tarde na Curva 1.")
        )
        assert radio.has_note

        radio.show_thinking()
        assert radio._text.text() != THINKING_TEXT  # noqa: SLF001
        assert radio.has_note, "a nota foi apagada por um pedido pendente"

    def test_o_cartao_do_radio_pinta_o_proprio_fundo(self, qt_app) -> None:  # noqa: ANN001
        """QWidget nu ignora `background-color` sem fundo estilizado.

        Sem o atributo, o cartão fica invisível na tela — só o texto solto.
        """
        from PySide6.QtCore import Qt

        from gt7app.design.tokens import DARK_THEME
        from gt7app.widgets.radio import RadioCard

        radio = RadioCard(DARK_THEME)
        assert radio.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


class TestSemEngenheiro:
    """O `gt7ai` pode simplesmente não estar instalado."""

    def test_servico_sem_engenheiro_nao_estoura(self, qt_app) -> None:  # noqa: ANN001
        from gt7app.services.engineer import EngineerService

        service = EngineerService(None)
        assert not service.is_available

        service.request_debrief(None, track="X")
        service.request_session_report(None, track="X")
        service.request_quick_note("algo")
        service.new_lap()
        service.new_session()
        service.shutdown()

        assert not service.is_busy

    def test_ciclo_de_vida_chega_ao_engenheiro(self, qt_app) -> None:  # noqa: ANN001
        """Sem isto a cadência do rádio nunca reseta e ele emudece."""
        from gt7app.services.engineer import EngineerService

        engineer = FakeEngineer()
        service = EngineerService(engineer)
        service.new_lap()
        service.new_lap()
        service.new_session()

        assert engineer.laps == 2
        assert engineer.sessions == 1


class TestGraficoNaoEQuadratico:
    """A regressão de desempenho que a Fase 8 revelou ao medir.

    `_to_pixel` chamava `_y_bounds()` por ponto, e `_y_bounds` varre todas as
    séries inteiras. Com ~6000 amostras a repintura levava 938 ms — quase um
    segundo de janela congelada toda vez que algo mudava o layout.
    """

    def _chart(self, points: int):  # noqa: ANN202
        from gt7app.design.tokens import DARK_THEME
        from gt7app.widgets.charts import DistanceChart, Series

        chart = DistanceChart(DARK_THEME, "t", unit="u")
        chart.resize(900, 300)
        chart.set_series(
            [
                Series("a", "#ffffff", [(float(i), float(i % 200)) for i in range(points)]),
                Series("b", "#000000", [(float(i), float(i % 137)) for i in range(points)]),
            ]
        )
        return chart

    def test_a_faixa_vertical_e_calculada_uma_vez_por_conjunto(self, qt_app) -> None:  # noqa: ANN001
        """Zero chamadas durante a pintura: o valor já está memorizado."""
        chart = self._chart(6000)

        calls = 0
        original = type(chart)._y_bounds  # noqa: SLF001

        def counting(self):  # noqa: ANN001, ANN202
            nonlocal calls
            calls += 1
            return original(self)

        type(chart)._y_bounds = counting  # noqa: SLF001
        try:
            chart.grab()
            assert calls == 0, (
                f"_y_bounds chamada {calls}x durante a pintura — "
                "a repintura voltou a ser quadrática"
            )
        finally:
            type(chart)._y_bounds = original  # noqa: SLF001

    def test_a_pintura_escala_linearmente(self, qt_app) -> None:  # noqa: ANN001
        """Dez vezes mais pontos não pode custar cem vezes mais tempo."""
        pequeno, grande = self._chart(600), self._chart(6000)

        pequeno.grab()  # aquece
        grande.grab()

        t0 = time.monotonic()
        pequeno.grab()
        tempo_pequeno = time.monotonic() - t0

        t0 = time.monotonic()
        grande.grab()
        tempo_grande = time.monotonic() - t0

        # Quadrático daria ~100x, então o teto de 18x continua pegando a
        # regressão com folga enorme. Era 12x, e falhava em ~1 de cada 4
        # execuções medindo 12,4x: com 600 pontos o custo fixo da pintura pesa
        # mais que o dos pontos, o que infla a razão sem que nada tenha piorado.
        # Um teste de desempenho que falha por carga da máquina ensina a ignorar
        # falha de desempenho, que é o oposto do que ele existe para fazer.
        assert tempo_grande < max(0.05, tempo_pequeno * 18), (
            f"600 pontos: {tempo_pequeno * 1000:.1f} ms, "
            f"6000 pontos: {tempo_grande * 1000:.1f} ms"
        )

    def test_a_faixa_acompanha_a_troca_de_series(self, qt_app) -> None:  # noqa: ANN001
        """Memorizar não pode congelar: série nova, faixa nova."""
        from gt7app.widgets.charts import Series

        chart = self._chart(100)
        antes = chart._bounds  # noqa: SLF001
        chart.set_series([Series("c", "#fff", [(0.0, 1000.0), (1.0, 2000.0)])])
        assert chart._bounds != antes  # noqa: SLF001
        assert chart._bounds[1] >= 2000.0  # noqa: SLF001

        chart.clear()
        assert chart._bounds == (0.0, 1.0)  # noqa: SLF001
