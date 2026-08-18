"""
Exclusão de voltas no Histórico.

A telemetria de uma volta são ~6.000 amostras que só existem porque alguém
pilotou, e não há desfazer. Por isso o que mais importa aqui não é que o
`DELETE` funcione — é o que acontece **em volta** dele: um clique sem seleção não
pode apagar tudo, e as outras páginas não podem continuar segurando uma volta
que já não existe.

A confirmação é substituída nos testes: um `QMessageBox` modal travaria a suíte
esperando um clique que nunca vem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="a página é Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Track  # noqa: E402
from gt7core.telemetry.sources.mock import synthetic_session  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def historico(app: QApplication, tmp_path: Path):  # noqa: ANN201, ARG001
    """Uma janela com quatro voltas gravadas numa pista."""
    settings = Settings()
    settings.storage.database_path = tmp_path / "h.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"

    core = build_core(settings)
    track_id = core.tracks.get_or_create("Interlagos")
    core.session_manager.set_track(Track(id=track_id, name="Interlagos"))
    core.session_manager.start_session()
    for frame in synthetic_session(lap_count=4):
        core.engine.on_frame(frame)
    core.session_manager.end_session()

    window = build_gui(core)
    page = next(p for p in window._pages if p.page_id == "history")  # noqa: SLF001
    page.refresh()
    # Confirmação sempre aceita: o modal real travaria a suíte.
    page._confirm = lambda *_args, **_kwargs: True  # type: ignore[method-assign]  # noqa: SLF001

    yield page, core, window

    # `closeEvent` já desmonta o núcleo e fecha o banco. Fechar de novo aqui
    # deixava a conexão marcada como morta enquanto os timers da janela antiga
    # ainda rodavam, e o teste seguinte estourava "banco já fechado" no setup.
    window.close()


class TestExcluirSelecionadas:
    def test_apaga_so_o_que_estava_marcado(self, historico) -> None:  # noqa: ANN001
        page, core, _window = historico
        antes = len(page._laps)  # noqa: SLF001
        assert antes >= 2, "a fixture precisa de voltas para o teste valer"

        page._table.selectRow(0)  # noqa: SLF001
        page._on_delete_selected()  # noqa: SLF001

        track_id = core.tracks.get_all()[0].id
        assert len(core.laps.get_by_track(track_id)) == antes - 1

    def test_sem_selecao_nao_apaga_nada(self, historico) -> None:  # noqa: ANN001
        """O botão fica ao lado de "Atualizar". Sem esta guarda, um clique
        distraído com a tabela vazia de seleção seria destrutivo."""
        page, core, _window = historico
        antes = len(page._laps)  # noqa: SLF001

        page._table.clearSelection()  # noqa: SLF001
        page._on_delete_selected()  # noqa: SLF001

        track_id = core.tracks.get_all()[0].id
        assert len(core.laps.get_by_track(track_id)) == antes
        assert "selecione" in page._note.text().lower()

    def test_a_tabela_encolhe_junto(self, historico) -> None:  # noqa: ANN001
        page, _core, _window = historico
        antes = page._table.rowCount()  # noqa: SLF001

        page._table.selectRow(0)  # noqa: SLF001
        page._on_delete_selected()  # noqa: SLF001

        assert page._table.rowCount() == antes - 1  # noqa: SLF001


class TestExcluirTudo:
    def test_limpa_a_pista_inteira(self, historico) -> None:  # noqa: ANN001
        page, core, _window = historico
        page._on_delete_all()  # noqa: SLF001

        track_id = core.tracks.get_all()[0].id
        assert core.laps.get_by_track(track_id) == []
        assert page._table.rowCount() == 0  # noqa: SLF001

    def test_pista_ja_vazia_avisa_em_vez_de_agir(self, historico) -> None:  # noqa: ANN001
        page, _core, _window = historico
        page._on_delete_all()  # noqa: SLF001
        page._on_delete_all()  # noqa: SLF001
        assert "não há voltas" in page._note.text()


class TestConsistenciaDasOutrasPaginas:
    def test_as_irmas_sao_marcadas_para_recarregar(self, historico) -> None:  # noqa: ANN001
        """Sem isto, Análise continuaria exibindo uma volta apagada — e o
        próximo clique nela buscaria amostras de um `lap_id` que sumiu."""
        page, _core, window = historico
        irmas = [p for p in window._pages if p is not page]  # noqa: SLF001
        for irma in irmas:
            irma._dirty = False  # noqa: SLF001

        page._table.selectRow(0)  # noqa: SLF001
        page._on_delete_selected()  # noqa: SLF001

        assert all(p._dirty or p.isVisible() for p in irmas), (  # noqa: SLF001
            "alguma página continuaria mostrando dados que não existem mais"
        )


class TestConfirmacao:
    def test_cancelar_preserva_tudo(self, historico) -> None:  # noqa: ANN001
        """Exclusão sem desfazer precisa de uma pergunta que signifique algo."""
        page, core, _window = historico
        page._confirm = lambda *_a, **_k: False  # type: ignore[method-assign]  # noqa: SLF001
        antes = len(page._laps)  # noqa: SLF001

        page._table.selectRow(0)  # noqa: SLF001
        page._on_delete_selected()  # noqa: SLF001
        page._on_delete_all()  # noqa: SLF001

        track_id = core.tracks.get_all()[0].id
        assert len(core.laps.get_by_track(track_id)) == antes
