"""
Composition root — o único lugar que instancia classes concretas.

Todas as demais recebem suas dependências por construtor. É isso que torna a
arquitetura verificável: se alguma camada importasse uma implementação concreta
por conta própria, trocar SQLite por outra coisa exigiria caçar imports pelo
projeto inteiro.

A montagem é de baixo para cima, e a ordem conta uma história:

    configuração → observabilidade → armazenamento → barramento
    → motor de telemetria → sessão e gravação → fonte
    → adaptador Qt → ViewModels → janela

Note onde o Qt entra: só nos dois últimos passos. Tudo acima é `gt7core` puro e
funcionaria igual num bot do Discord ou num worker de IA sem interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gt7core.catalog import GameCatalog
from gt7core.config.settings import Settings
from gt7core.domain.models import Car
from gt7core.events.bus import EventBus
from gt7core.observability.logging import configure_logging, get_logger
from gt7core.observability.metrics import TelemetryMetrics
from gt7core.session.manager import RecordingService, SessionManager
from gt7core.storage.database import SqliteDatabase
from gt7core.storage.repositories import (
    SqliteCarRepository,
    SqliteLapRepository,
    SqliteSessionRepository,
    SqliteTrackRepository,
)
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
from gt7core.telemetry.sources.base import ConnectionState, TelemetrySource
from gt7core.telemetry.sources.factory import create_telemetry_source

if TYPE_CHECKING:
    # Só para tipagem: importar a casca aqui em tempo de execução arrastaria o
    # Qt para dentro deste módulo, que precisa ser importável sem interface.
    from .shell import AppShell

_log = get_logger(__name__)


@dataclass(slots=True)
class CoreApplication:
    """O núcleo montado, sem nenhuma dependência de interface.

    Existe como objeto para que a casca gráfica — ou um bot, ou um teste —
    receba tudo pronto e apenas assine o que lhe interessa.
    """

    settings: Settings
    bus: EventBus
    database: SqliteDatabase
    laps: SqliteLapRepository
    sessions: SqliteSessionRepository
    tracks: SqliteTrackRepository
    cars: SqliteCarRepository
    engine: TelemetryEngine
    session_manager: SessionManager
    recording: RecordingService
    source: TelemetrySource
    metrics: TelemetryMetrics
    catalog: GameCatalog

    def start(self) -> None:
        """Abre a sessão e liga a captura."""
        self.engine.reset()
        self.recording.reload_reference()
        self.session_manager.start_session()
        self.source.start()

    def stop(self) -> None:
        """Encerra captura e sessão, nesta ordem — parar a fonte primeiro evita
        que uma volta chegue depois de a sessão ter sido fechada."""
        self.source.stop()
        self.session_manager.end_session()
        self.engine.reset()

    def close(self) -> None:
        self.stop()
        self.database.close()


def build_core(
    settings: Settings | None = None,
    *,
    replay_path: str | Path | None = None,
) -> CoreApplication:
    """Monta o núcleo a partir da configuração.

    Sem Qt em lugar nenhum: este grafo sobe num teste, num bot ou num servidor
    exatamente como sobe atrás da interface.
    """
    settings = settings or Settings.load()
    configure_logging(
        settings.logging.level,
        json_format=settings.logging.json_format,
        file_path=settings.logging.file_path,
    )

    database = SqliteDatabase(settings.storage.database_path)
    laps = SqliteLapRepository(
        database,
        keep_recent_per_track=settings.storage.keep_recent_per_track,
        keep_best_per_track=settings.storage.keep_best_per_track,
    )
    sessions = SqliteSessionRepository(database)
    tracks = SqliteTrackRepository(database)
    cars = SqliteCarRepository(database)

    catalog = GameCatalog()

    bus = EventBus()
    engine = TelemetryEngine(bus)
    session_manager = SessionManager(bus, sessions)
    recording = RecordingService(bus, laps, session_manager)

    metrics = TelemetryMetrics()
    source = create_telemetry_source(
        settings, replay_path=replay_path, metrics=metrics
    )
    source.on_frame(engine.on_frame)

    # O delta precisa da distância corrente, que só o motor conhece — por isso é
    # publicado a partir do fluxo de telemetria, não de dentro do motor. Mantém
    # o motor sem saber que existe comparação.
    def publish_delta(event: TelemetryReceived) -> None:
        recording.publish_delta(event.point.distance_m, event.point.elapsed_ms)

    bus.subscribe(TelemetryReceived, publish_delta)

    # O carro se identifica sozinho. O protocolo manda um `car_id` numérico e
    # mais nada; sem o catálogo, o histórico inteiro fica com "carro 24" e o
    # debrief do engenheiro nunca preenche o campo do carro.
    #
    # Este retorno de chamada é **puro**: consulta um dicionário em memória e
    # publica. Nenhum acesso a banco, e a razão é concreta. A primeira versão
    # chamava `cars.get_or_create()` aqui, e isto roda na thread de captura —
    # a mesma conexão SQLite passou a ser usada por duas threads ao mesmo tempo,
    # e o sintoma não foi exceção: foi segmentation fault. Pior, um quadro
    # atrasado chegando depois de `close()` tocava um banco já fechado.
    #
    # O id local do carro fica para quando alguma tela precisar dele; hoje o que
    # se usa é o **nome**, que viaja em `CarChanged` e na sessão em memória.
    last_game_car: list[int] = []

    def name_car(event: TelemetryReceived) -> None:
        # O id vem do quadro cru, não da amostra: `TelemetryPoint` guarda o que
        # descreve a pilotagem, e qual carro é não muda de amostra para amostra.
        game_car_id = event.frame.car_id
        if game_car_id < 0 or last_game_car[-1:] == [game_car_id]:
            return
        last_game_car.append(game_car_id)

        session_manager.set_car(
            Car(
                name=catalog.car_name(game_car_id) or f"Carro {game_car_id}",
                maker=catalog.car_maker(game_car_id),
            )
        )

    bus.subscribe(TelemetryReceived, name_car)

    unfinished = sessions.find_unfinished()
    if unfinished:
        # §8: recuperação após falha. Não fecha automaticamente — a sessão pode
        # ser legítima de outra janela aberta agora. Só reporta.
        _log.warning(
            "sessões não encerradas encontradas",
            extra={"count": len(unfinished), "ids": [s.id for s in unfinished]},
        )

    _log.info("núcleo montado", extra={"source": settings.telemetry.source})

    return CoreApplication(
        settings=settings,
        bus=bus,
        database=database,
        laps=laps,
        sessions=sessions,
        tracks=tracks,
        cars=cars,
        engine=engine,
        session_manager=session_manager,
        recording=recording,
        source=source,
        metrics=metrics,
        catalog=catalog,
    )


def build_gui(core: CoreApplication) -> AppShell:
    """Monta a casca gráfica sobre um núcleo já pronto.

    O import do Qt acontece **dentro da função** de propósito: assim este módulo
    continua importável (e o núcleo, montável) num ambiente sem interface
    gráfica instalada — que é exatamente o caso de um servidor ou de um teste.
    """
    from .adapters.qt_bus import QtEventBusAdapter
    from .shell import AppShell
    from .viewmodels.live import LiveViewModel

    adapter = QtEventBusAdapter(core.bus)
    live_vm = LiveViewModel(adapter)

    # Estado de conexão vem direto da fonte, não do barramento: é um fato da
    # captura, não do domínio, e não faz sentido um bot de Discord assiná-lo.
    core.source.on_status(
        lambda state, message: live_vm.on_connection_state(
            ConnectionState(state), message
        )
    )

    return AppShell(core, live_vm, adapter)
