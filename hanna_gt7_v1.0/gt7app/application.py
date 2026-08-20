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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gt7core.analytics.live import LiveEventDetector, RaceEventDetected
from gt7core.catalog import GameCatalog
from gt7core.config.settings import Settings
from gt7core.domain.models import Car, Track
from gt7core.events.bus import EventBus
from gt7core.observability.logging import configure_logging, get_logger
from gt7core.observability.metrics import TelemetryMetrics
from gt7core.session.manager import CarChanged, RecordingService, SessionManager
from gt7core.storage.database import SqliteDatabase
from gt7core.storage.repositories import (
    SqliteCarRepository,
    SqliteLapRepository,
    SqliteSessionRepository,
    SqliteTrackRepository,
)
from gt7core.telemetry.engine import (
    LapBoundaryDetected,
    TelemetryEngine,
    TelemetryReceived,
)
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
    engineer: Any | None
    """O Race Engineer, ou None se o pacote `gt7ai` não estiver instalado.

    Tipado como `Any` de propósito: anotar com `RaceEngineer` exigiria importar
    `gt7ai` no topo deste módulo, e a aplicação deixaria de subir sem o plugin —
    que é justamente o oposto do que o §49 pede.
    """

    live_detector: LiveEventDetector = field(default_factory=LiveEventDetector)
    """Detector de eventos da volta em andamento. Python puro, sem Qt."""

    discord_bot: Any | None = None
    """Bot do Discord, ou None quando desligado, sem token ou sem a biblioteca.

    Mora fora de `build_core` pelo mesmo motivo do `engineer_service`: subir uma
    conexão de rede não é responsabilidade de montar o grafo. Quem liga é
    `start()`, e falhar não impede a captura.
    """

    _closed: bool = False
    """Guarda de desmonte — ver `close`."""

    engineer_service: Any | None = None
    """Ponte Qt para o engenheiro. Preenchida por `build_gui`, nunca aqui.

    O engenheiro em si é Python puro e nasce em `build_core`; o serviço é um
    `QObject` e não pode nascer junto, porque este grafo precisa subir num bot
    ou num teste headless. A separação é a mesma que existe entre `EventBus` e
    `QtEventBusAdapter`: o núcleo produz, a casca entrega na thread certa.
    """

    def start(self) -> None:
        """Abre a sessão e liga a captura."""
        self._start_discord()
        self.engine.reset()
        self.recording.reload_reference()
        self.session_manager.start_session()
        # Sessão nova esquece tudo, inclusive a convenção de unidade do canal
        # de escorregamento: o piloto pode ter trocado de carro.
        self.live_detector.reset()
        if self.engineer is not None:
            # Zera livro-caixa e cadência: o teto de gasto e o silêncio mínimo
            # entre notas são por sessão, não por execução do programa.
            self.engineer.new_session()
        self.source.start()

    def stop(self) -> None:
        """Encerra captura e sessão, nesta ordem — parar a fonte primeiro evita
        que uma volta chegue depois de a sessão ter sido fechada."""
        self.source.stop()
        self.session_manager.end_session()
        self.engine.reset()

    def reconfigure_source(self, *, replay_path: str | Path | None = None) -> None:
        """Remonta a fonte a partir de `self.settings`, sem reiniciar o programa.

        Trocar do gerador sintético para o PS5 é a operação que a tela de
        configuração existe para permitir, e exigir um reinício a cada tentativa
        de IP tornaria o acerto da rede — que já é a parte chata — insuportável.

        A ordem importa. A fonte antiga para **antes** de a nova nascer: duas
        fontes vivas escrevendo no mesmo motor entregariam quadros sintéticos
        misturados com os do console, e o resultado seria uma volta que não
        aconteceu. E a nova só sobe se a antiga estava rodando, para que abrir a
        tela de configuração e salvar não comece uma captura que ninguém pediu.

        Se a configuração nova for inválida — `udp` sem IP, por exemplo — o
        `create_telemetry_source` levanta antes de qualquer estado mudar, e a
        fonte antiga continua sendo a fonte: um erro de digitação não pode
        deixar o programa sem captura nenhuma.
        """
        was_running = self.source.is_running
        new_source = create_telemetry_source(
            self.settings, replay_path=replay_path, metrics=self.metrics
        )

        old_source = self.source
        old_source.stop()
        new_source.adopt_callbacks_from(old_source)
        self.source = new_source

        _log.info(
            "fonte de telemetria trocada",
            extra={"source": self.settings.telemetry.source, "running": was_running},
        )
        if was_running:
            new_source.start()

    def _start_discord(self) -> None:
        """Sobe o bot, se houver. Nada aqui pode impedir a captura de começar.

        O `try` largo é deliberado: entre token inválido, rede indisponível e
        biblioteca com versão incompatível há muitas formas de o Discord falhar,
        e nenhuma delas justifica o piloto não conseguir gravar a sessão.
        """
        if self.discord_bot is not None:
            return
        try:
            from gt7discord import build_bot
        except ImportError:
            return

        try:
            bot = build_bot(self)
            if bot is not None:
                bot.start()
                self.discord_bot = bot
        except Exception:
            _log.warning("o bot do Discord não pôde subir", exc_info=True)

    def close(self) -> None:
        """Desmonta tudo. **Idempotente**: fechar duas vezes é um caso normal.

        Não é defensividade genérica; é o desenho real do desmonte. A janela
        fecha o núcleo no `closeEvent`, e quem montou o núcleo também o fecha
        num `finally` — que é o certo, porque a janela pode nem ter chegado a
        existir. As duas coisas acontecerem é o caminho comum, não a exceção.

        Sem esta guarda, a segunda chamada percorre `stop()` até
        `end_session()`, que grava no banco já fechado e levanta
        `sqlite3.ProgrammingError: banco já fechado` — no meio de um `finally`,
        onde a exceção mascara o que quer que estivesse sendo tratado. Já
        aconteceu duas vezes neste projeto: uma numa fixture de teste, outra na
        auditoria de casos de uso.
        """
        if self._closed:
            return
        self._closed = True

        self.stop()
        if self.discord_bot is not None:
            self.discord_bot.stop()
            self.discord_bot = None
        self.database.close()


def _build_engineer(settings: Settings) -> Any | None:
    """Monta o Race Engineer, ou devolve None se o plugin não existir.

    O import é local e protegido por duas razões distintas, e as duas importam:

    `ImportError` cobre o pacote não estar instalado — a aplicação tem que subir
    sem `gt7ai`, que é o §49 aplicado ao nível de empacotamento e não só de
    arquitetura.

    A exceção larga cobre o resto. `RaceEngineer.from_settings` já promete não
    estourar, mas isto roda na inicialização: se a promessa falhar por qualquer
    motivo, o preço não pode ser o programa inteiro não abrir — é justamente o
    tipo de acoplamento que a IA como módulo adicional existe para evitar.
    """
    try:
        from gt7ai import RaceEngineer
    except ImportError:
        _log.info("gt7ai não instalado — a aplicação roda sem engenheiro")
        return None

    try:
        engineer = RaceEngineer.from_settings(settings)
    except Exception:  # pragma: no cover - from_settings já degrada sozinho
        _log.exception("falha ao montar o engenheiro; seguindo sem ele")
        return None

    _log.info(
        "engenheiro montado",
        extra={"online": engineer.is_online, "provider": settings.ai.provider},
    )
    return engineer


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
    engineer = _build_engineer(settings)
    live_detector = LiveEventDetector()

    bus = EventBus()
    engine = TelemetryEngine(bus, sample_rate_hz=settings.telemetry.sample_rate_hz)
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

    # Detecção ao vivo. Roda na thread de captura e é O(1) por amostra — o
    # detector nunca varre o histórico, justamente porque este retorno de
    # chamada acontece 60 vezes por segundo ao lado da gravação.
    #
    # O núcleo **publica** o evento e para por aí. Quem pede uma nota ao
    # engenheiro é a casca; quem manda no Discord será o bot. Essa indiferença é
    # o que faz o mesmo detector servir aos três sem nenhum `if` aqui dentro.
    def detect_live(event: TelemetryReceived) -> None:
        for race_event in live_detector.feed(event.point):
            bus.publish(RaceEventDetected(event=race_event))

    bus.subscribe(TelemetryReceived, detect_live)

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

    # A cota de notas de rádio é por volta, e alguém precisa virar a página. É
    # fiação de ciclo de vida, então mora aqui e não dentro de um plugin: o bot
    # do Discord e a interface consomem a mesma política em vez de cada um
    # inventar a sua.
    def on_lap_boundary(_event: LapBoundaryDetected) -> None:
        # A cota de notas é por volta, e o detector fecha o que estava aberto.
        # É fiação de ciclo de vida, então mora aqui e não dentro de um plugin:
        # o bot do Discord e a interface consomem a mesma política em vez de
        # cada um inventar a sua.
        live_detector.new_lap()
        if engineer is not None:
            engineer.new_lap()

    bus.subscribe(LapBoundaryDetected, on_lap_boundary)

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
        engineer=engineer,
        live_detector=live_detector,
    )


def build_gui(core: CoreApplication) -> AppShell:
    """Monta a casca gráfica sobre um núcleo já pronto.

    O import do Qt acontece **dentro da função** de propósito: assim este módulo
    continua importável (e o núcleo, montável) num ambiente sem interface
    gráfica instalada — que é exatamente o caso de um servidor ou de um teste.
    """
    from .adapters.qt_bus import QtEventBusAdapter
    from .services.engineer import EngineerService
    from .shell import AppShell
    from .viewmodels.live import LiveViewModel

    adapter = QtEventBusAdapter(core.bus)
    live_vm = LiveViewModel(adapter)

    # O serviço existe mesmo sem engenheiro: as páginas perguntam
    # `is_available` e mostram "não instalado" em vez de checarem None por toda
    # parte.
    core.engineer_service = EngineerService(core.engineer)

    # Estado de conexão vem direto da fonte, não do barramento: é um fato da
    # captura, não do domínio, e não faz sentido um bot de Discord assiná-lo.
    core.source.on_status(
        lambda state, message: live_vm.on_connection_state(
            ConnectionState(state), message
        )
    )

    # ---- persistência do carro e da pista, na thread da interface ----
    #
    # A detecção acontece no núcleo, na thread de captura, e é **pura** de
    # propósito: a primeira versão gravava no banco de lá e o sintoma não foi
    # exceção, foi segmentation fault. Mas puro também significa que nada era
    # gravado — o carro era identificado, aparecia na tela, e o histórico
    # continuava sem saber qual era. Gravar aqui fecha a lacuna sem reabrir o
    # defeito: o adaptador entrega na thread da interface, que é a mesma que já
    # fala com o SQLite.

    def persist_car(event: CarChanged) -> None:
        if not event.car_name:
            return
        car_id = core.cars.get_or_create(event.car_name)
        car = core.session_manager.car
        if car is not None and car.id is None:
            core.session_manager.set_car(
                Car(id=car_id, name=car.name, maker=car.maker)
            )

    adapter.subscribe(CarChanged, persist_car)

    def detect_track(event: LapBoundaryDetected) -> None:
        """Nomeia a pista pelo **comprimento** da primeira volta fechada.

        O GT7 não transmite o nome do circuito — nem um id dele. O que dá para
        medir é a distância percorrida na volta, e o catálogo sabe o
        comprimento de 105 pistas.

        Só assume quando o candidato é **único** dentro da tolerância. Vários
        circuitos têm comprimento parecido, e batizar a sessão com o palpite
        errado é pior que deixá-la sem nome: o histórico passa a misturar voltas
        de pistas diferentes sob um rótulo, e nada na tela denuncia.
        """
        if core.session_manager.track is not None:
            return
        candidatos = core.catalog.guess_by_length(event.distance_m)
        if not candidatos:
            return

        nomes = [c.name for c in candidatos]
        if len(candidatos) > 1:
            # Vários circuitos compartilham comprimento, e batizar a sessão com
            # o palpite errado é pior que deixá-la sem nome: o histórico passa a
            # misturar voltas de pistas diferentes sob um rótulo, sem nada na
            # tela denunciando. Então **sugere** em vez de decidir — os
            # candidatos vão para o campo, o mais provável na frente, e um
            # clique confirma.
            _log.info(
                "pista ambígua pelo comprimento",
                extra={
                    "distance_m": round(event.distance_m, 1),
                    "candidatos": nomes[:3],
                },
            )
            shell.on_track_candidates(nomes)
            return

        nome = nomes[0]
        track_id = core.tracks.get_or_create(nome)
        core.session_manager.set_track(Track(id=track_id, name=nome))
        _log.info("pista identificada pelo comprimento", extra={"track": nome})
        shell.on_track_candidates(nomes)

    adapter.subscribe(LapBoundaryDetected, detect_track)

    shell = AppShell(core, live_vm, adapter)
    return shell
