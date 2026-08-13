"""
Ponto de entrada e composition root.

Este é o **único** arquivo que instancia classes concretas. Todas as demais
recebem suas dependências pelo construtor, na forma das interfaces do domínio.
É isso que torna a arquitetura verificável: se alguma camada importasse uma
implementação concreta por conta própria, a troca de SQLite por JSON exigiria
caçar imports por todo o projeto.

Para rodar (qualquer uma das formas funciona):
    python3 -m src.main
    python3 src/main.py
    python3 src
"""

import sys
from pathlib import Path

# Permite executar este arquivo diretamente (`python3 src/main.py`), que é o
# que qualquer pessoa tenta primeiro. Sem isto, os imports relativos abaixo
# falham com "attempted relative import with no known parent package": rodado
# como script, o Python não considera `src` um pacote.
#
# A correção põe a pasta-mãe no sys.path e declara o pacote, o que faz os
# imports relativos passarem a resolver normalmente.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"

from PySide6.QtWidgets import QApplication

from .application.events.event_bus import EventBus
from .application.services.session_health import SessionHealth
from .application.services.session_manager import SessionManager
from .application.services.telemetry_service import TelemetryService
from .application.viewmodels.comparison_viewmodel import ComparisonViewModel
from .application.viewmodels.history_viewmodel import HistoryViewModel
from .application.viewmodels.live_viewmodel import LiveViewModel
from .application.viewmodels.telemetry_viewmodel import TelemetryViewModel
from .domain.config import load_config
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

    # --- configuração ---
    # Lida uma vez, injetada em todo mundo. É o único ponto do app que conhece
    # o arquivo de configuração.
    config = load_config()

    # --- barramento ---
    event_bus = EventBus()

    # --- infraestrutura ---
    database = SqliteDatabase()
    lap_repository = SqliteLapRepository(
        database,
        num_sectors=config.num_sectors,
        keep_best=config.keep_best_per_track,
        keep_recent=config.keep_recent_per_track,
    )
    track_repository = SqliteTrackRepository(database)
    car_repository = SqliteCarRepository(database)

    catalog = CsvCatalog()
    track_catalog = CsvTrackRepository(catalog)
    car_catalog = CsvCarRepository(catalog)

    telemetry_source = Gt7TelemetrySource(config.ps_ip, config=config)

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
        config=config,
    )
    # Observa o fluxo que o app já recebe. Vive aqui, e não numa ferramenta
    # separada, porque UDP unicast entrega cada pacote a um socket só: uma
    # ferramenta externa escutando a mesma porta rouba o fluxo do app.
    session_health = SessionHealth(event_bus)

    live_vm = LiveViewModel(event_bus, config)
    history_vm = HistoryViewModel(lap_repository, event_bus)
    comparison_vm = ComparisonViewModel(lap_repository, event_bus)
    # O repositório de pistas entra aqui para a aba respeitar os limites de
    # setor configurados por pista, em vez de sempre dividir em partes iguais.
    telemetry_vm = TelemetryViewModel(
        lap_repository, event_bus, track_repository, config
    )

    # --- apresentação ---
    # Fábricas, não instâncias: a janela decide quando construir cada aba e não
    # precisa saber qual ViewModel cada uma consome.
    tab_factories = {
        "Ao Vivo": lambda: LiveDashboardTab(live_vm),
        "Histórico": lambda: HistoryTab(history_vm),
        "Telemetria": lambda: TelemetryTab(telemetry_vm),
        "Comparação": lambda: ComparisonTab(comparison_vm),
    }

    def on_config_changed(nova) -> None:
        """Propaga o que pode mudar sem reiniciar o app.

        Fonte de telemetria e ViewModels leem a config a cada uso; o
        repositório e a thread de captura leem na construção, então esses
        valores só valem na próxima sessão.
        """
        telemetry_source._config = nova
        telemetry_service._config = nova
        live_vm._config = nova
        telemetry_vm._config = nova
        telemetry_vm._series_cache.clear()

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
        config=config,
        on_config_changed=on_config_changed,
        session_health=session_health,
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HANNA GT7 AI")

    window = build_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
