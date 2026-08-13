"""
Ponto de entrada e composition root.

Este é o **único** arquivo que instancia classes concretas. Todas as demais
recebem suas dependências pelo construtor, na forma das interfaces do domínio.
É isso que torna a arquitetura verificável: se alguma camada importasse uma
implementação concreta por conta própria, a troca de SQLite por JSON exigiria
caçar imports por todo o projeto.

Para rodar:
    python -m src.main
"""

import sys

from PySide6.QtWidgets import QApplication

from .application.events.event_bus import EventBus
from .application.services.session_manager import SessionManager
from .application.services.telemetry_service import TelemetryService
from .application.viewmodels.comparison_viewmodel import ComparisonViewModel
from .application.viewmodels.history_viewmodel import HistoryViewModel
from .application.viewmodels.live_viewmodel import LiveViewModel
from .application.viewmodels.telemetry_viewmodel import TelemetryViewModel
from .infrastructure.repositories.csv_car_repository import CsvCarRepository
from .infrastructure.repositories.csv_catalog import CsvCatalog
from .infrastructure.repositories.csv_track_repository import CsvTrackRepository
from .infrastructure.repositories.sqlite_car_repository import SqliteCarRepository
from .infrastructure.repositories.sqlite_database import SqliteDatabase
from .infrastructure.repositories.sqlite_lap_repository import SqliteLapRepository
from .infrastructure.repositories.sqlite_track_repository import SqliteTrackRepository
from .infrastructure.telemetry.listener_thread import Gt7TelemetrySource
from .presentation.main_window import DEFAULT_PS_IP, MainWindow
from .presentation.tabs.comparison_tab import ComparisonTab
from .presentation.tabs.history_tab import HistoryTab
from .presentation.tabs.live_tab import LiveDashboardTab
from .presentation.tabs.telemetry_tab import TelemetryTab


def build_application() -> MainWindow:
    """Monta o grafo de dependências, de baixo para cima."""

    # --- barramento ---
    event_bus = EventBus()

    # --- infraestrutura ---
    database = SqliteDatabase()
    lap_repository = SqliteLapRepository(database)
    track_repository = SqliteTrackRepository(database)
    car_repository = SqliteCarRepository(database)

    catalog = CsvCatalog()
    track_catalog = CsvTrackRepository(catalog)
    car_catalog = CsvCarRepository(catalog)

    telemetry_source = Gt7TelemetrySource(DEFAULT_PS_IP)

    # --- aplicação ---
    session_manager = SessionManager(event_bus)
    telemetry_service = TelemetryService(
        telemetry_source=telemetry_source,
        lap_repository=lap_repository,
        session_manager=session_manager,
        event_bus=event_bus,
        track_catalog=track_catalog,
        # Só a função de resolver nome, não o repositório inteiro: o serviço
        # precisa de "id -> Montadora Modelo", nada mais.
        car_name_resolver=car_catalog.get_full_name,
    )

    live_vm = LiveViewModel(event_bus)
    history_vm = HistoryViewModel(lap_repository, event_bus)
    comparison_vm = ComparisonViewModel(lap_repository, event_bus)
    telemetry_vm = TelemetryViewModel(lap_repository, event_bus)

    # --- apresentação ---
    # Fábricas, não instâncias: a janela decide quando construir cada aba e não
    # precisa saber qual ViewModel cada uma consome.
    tab_factories = {
        "Ao Vivo": lambda: LiveDashboardTab(live_vm),
        "Histórico": lambda: HistoryTab(history_vm),
        "Telemetria": lambda: TelemetryTab(telemetry_vm),
        "Comparação": lambda: ComparisonTab(comparison_vm),
    }

    def on_track_changed(track_id: int | None) -> None:
        """Propaga a troca de pista para os ViewModels que filtram por ela."""
        history_vm.set_track(track_id)
        comparison_vm.set_track(track_id)
        telemetry_vm.set_track(track_id)

    return MainWindow(
        telemetry_service=telemetry_service,
        session_manager=session_manager,
        event_bus=event_bus,
        track_repository=track_repository,
        car_repository=car_repository,
        track_catalog=track_catalog,
        tab_factories=tab_factories,
        on_track_changed=on_track_changed,
        set_ps_ip=telemetry_source.set_ps_ip,
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HANNA GT7 AI")

    window = build_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
