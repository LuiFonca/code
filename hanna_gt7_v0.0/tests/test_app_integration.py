"""
Testes de integração da aplicação montada — núcleo e, quando há Qt, interface.

O teste de fumaça da janela é o que fecha a Fase 3: prova que a fatia vertical
inteira funciona sobre o núcleo novo — fonte → motor → barramento → adaptador
→ ViewModel → widgets — com telemetria de verdade correndo e volta indo para o
banco.

Os testes do núcleo rodam sempre; os da interface pulam sem PySide6, que é a
demonstração de que o núcleo não depende dela.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

from gt7app.application import build_core
from gt7core.config.settings import (
    LoggingConfig,
    Settings,
    StorageConfig,
    TelemetryConfig,
)
from gt7core.domain.models import Track
from gt7core.session.manager import LapSaved
from gt7core.telemetry.engine import TelemetryReceived

HAS_QT = importlib.util.find_spec("PySide6") is not None


def make_settings(tmp_path: Path, **telemetry: object) -> Settings:
    return Settings(
        telemetry=TelemetryConfig(**telemetry),  # type: ignore[arg-type]
        storage=StorageConfig(
            database_path=tmp_path / "test.db",
            telemetry_path=tmp_path / "telemetry",
            keep_recent_per_track=20,
            keep_best_per_track=5,
        ),
        logging=LoggingConfig(level="CRITICAL"),
    )


class TestNucleoMontado:
    """O grafo inteiro sobe sem Qt — é o que permite bot, worker e servidor."""

    def test_monta_com_fonte_sintetica(self, tmp_path: Path) -> None:
        core = build_core(make_settings(tmp_path))
        try:
            assert core.database is not None
            assert core.source.is_running is False
            assert core.settings.storage.keep_recent_per_track == 20
        finally:
            core.close()

    def test_start_abre_sessao_no_banco(self, tmp_path: Path) -> None:
        core = build_core(make_settings(tmp_path))
        try:
            core.start()
            assert core.session_manager.session_id is not None
            assert core.source.is_running is True
        finally:
            core.close()

    def test_stop_encerra_a_sessao(self, tmp_path: Path) -> None:
        core = build_core(make_settings(tmp_path))
        try:
            core.start()
            session_id = core.session_manager.session_id
            core.stop()

            assert session_id is not None
            stored = core.sessions.get_by_id(session_id)
            assert stored is not None
            assert stored.is_active is False
        finally:
            core.close()

    def test_telemetria_flui_e_volta_e_gravada(self, tmp_path: Path) -> None:
        """De ponta a ponta, sem interface: fonte → motor → banco."""
        # 400x: uma volta de 102 s fecha em ~0,25 s.
        core = build_core(make_settings(tmp_path, mock_speed_multiplier=400.0))
        try:
            track_id = core.tracks.get_or_create("Circuito de Teste")
            core.session_manager.set_track(Track(id=track_id, name="Circuito de Teste"))

            frames: list[TelemetryReceived] = []
            saved: list[LapSaved] = []
            core.bus.subscribe(TelemetryReceived, frames.append)
            core.bus.subscribe(LapSaved, saved.append)

            core.start()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and not saved:
                time.sleep(0.05)
            core.stop()

            assert len(frames) > 100, "telemetria não chegou"
            assert saved, "nenhuma volta foi gravada"
            assert core.laps.get_by_track(track_id)
        finally:
            core.close()

    def test_recuperacao_detecta_sessao_nao_encerrada(self, tmp_path: Path) -> None:
        """§8: o app caiu no meio; a sessão fica sem `ended_at`."""
        settings = make_settings(tmp_path)

        first = build_core(settings)
        first.start()
        # Fecha o banco sem chamar stop() — simula queda.
        first.database.close()

        second = build_core(settings)
        try:
            assert len(second.sessions.find_unfinished()) == 1
        finally:
            second.close()

    def test_replay_alimenta_o_mesmo_grafo(self, tmp_path: Path) -> None:
        """A aplicação não distingue replay de ao vivo (§40)."""
        from gt7core.telemetry.recording import SessionRecorder
        from gt7core.telemetry.sources.mock import synthetic_lap

        recording = tmp_path / "s.gt7rec"
        with SessionRecorder(recording) as recorder:
            for frame in synthetic_lap(lap_time_ms=1_500):
                recorder.record(frame)

        core = build_core(
            make_settings(tmp_path, source="replay"), replay_path=recording
        )
        try:
            frames: list[TelemetryReceived] = []
            core.bus.subscribe(TelemetryReceived, frames.append)

            core.start()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and len(frames) < 50:
                time.sleep(0.05)
            core.stop()

            assert len(frames) >= 50
        finally:
            core.close()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 não instalado — o núcleo é headless")
class TestJanelaAoVivo:
    """Fumaça da interface, com telemetria real correndo."""

    def test_janela_monta_e_exibe_telemetria(self, tmp_path: Path) -> None:
        import sys
        import traceback

        from PySide6.QtWidgets import QApplication

        from gt7app.application import build_gui

        errors: list[str] = []
        original_hook = sys.excepthook
        sys.excepthook = lambda *args: errors.append(
            "".join(traceback.format_exception(*args))
        )

        app = QApplication.instance() or QApplication([])
        core = build_core(make_settings(tmp_path))
        window = build_gui(core)

        try:
            window.show()
            app.processEvents()

            window._track_input.setCurrentText("Circuito de Teste")  # noqa: SLF001
            window._on_start()  # noqa: SLF001

            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.05)
                if window._cards["speed"]._value.text() != "—":  # noqa: SLF001
                    break

            # O painel está exibindo telemetria de verdade, vinda do núcleo
            # através do adaptador de thread.
            speed_text = window._cards["speed"]._value.text()  # noqa: SLF001
            assert speed_text != "—", "o painel não recebeu telemetria"
            assert "km/h" in speed_text

            assert core.session_manager.session_id is not None
            assert core.engine.buffered_points > 0

            window._on_stop()  # noqa: SLF001
            app.processEvents()
            assert errors == [], f"exceções na interface: {errors}"
        finally:
            sys.excepthook = original_hook
            window.close()
            app.processEvents()

    def test_fechar_a_janela_desmonta_tudo(self, tmp_path: Path) -> None:
        """Sem desmonte, o barramento seguiria emitindo para objetos Qt já
        destruídos — ponteiro morto, não exceção Python."""
        from PySide6.QtWidgets import QApplication

        from gt7app.application import build_gui

        app = QApplication.instance() or QApplication([])
        core = build_core(make_settings(tmp_path))
        window = build_gui(core)
        window.show()
        app.processEvents()

        assert core.bus.handler_count(TelemetryReceived) > 0

        window.close()
        app.processEvents()

        # O adaptador se desinscreveu; sobra apenas o handler do delta, que é do
        # núcleo e não da interface.
        assert core.bus.handler_count(LapSaved) == 0
