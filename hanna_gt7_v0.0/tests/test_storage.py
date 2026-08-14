"""
Testes de persistência: banco, repositórios, retenção e sessões.

Dois achados da auditoria são fixados aqui:

- **P8** — a retenção apagava voltas do usuário em silêncio, com limites fixos
  no código. Agora é configuração, e `0` desliga.
- **P9** — sessões não eram persistidas, o que tornava impossível a recuperação
  após falha pedida no §8.

O teste de transação é o mais importante do arquivo: a versão anterior à
refatoração fazia dois commits, e uma falha entre eles deixava volta gravada sem
setores — estado que o histórico exibia sem erro nenhum.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from gt7core.domain.models import Car, Lap, TelemetryPoint, Track
from gt7core.events.bus import EventBus
from gt7core.storage.database import SqliteDatabase, compute_sector_times
from gt7core.storage.repositories import (
    SqliteCarRepository,
    SqliteLapRepository,
    SqliteSessionRepository,
    SqliteTrackRepository,
)
from gt7core.telemetry.engine import TelemetryEngine
from gt7core.telemetry.sources.mock import synthetic_lap


@pytest.fixture
def db() -> SqliteDatabase:
    """Banco em memória — o caminho injetado é o que torna isto possível."""
    return SqliteDatabase(":memory:")


@pytest.fixture
def laps(db: SqliteDatabase) -> SqliteLapRepository:
    return SqliteLapRepository(db, keep_recent_per_track=20, keep_best_per_track=5)


def make_points(count: int = 50, *, base_ms: int = 0) -> list[TelemetryPoint]:
    return [
        TelemetryPoint(
            elapsed_ms=base_ms + i * 100,
            distance_m=i * 10.0,
            speed_kmh=150.0 + i,
            rpm=6000.0, gear=4, throttle=80.0, brake=0.0, fuel_level=50.0 - i * 0.01,
            tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
            position_x=float(i), position_z=float(i),
            g_lateral=0.1, g_longitudinal=0.2,
            suspension_fl=0.1, suspension_fr=0.1,
            suspension_rl=0.1, suspension_rr=0.1,
            tire_slip_fl=1.0, tire_slip_fr=1.0, tire_slip_rl=1.0, tire_slip_rr=1.0,
            turbo_boost=1.0, oil_temp=100.0, water_temp=90.0,
        )
        for i in range(count)
    ]


def make_lap(track_id: int, lap_time_ms: int, *, points: int = 50) -> Lap:
    return Lap(
        track_id=track_id,
        lap_time_ms=lap_time_ms,
        start_time=datetime.now(),
        points=make_points(points),
    )


class TestSchema:
    def test_cria_todas_as_tabelas(self, db: SqliteDatabase) -> None:
        tables = {
            r[0]
            for r in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert {"tracks", "cars", "sessions", "laps", "lap_frames", "sector_times"} <= tables

    def test_versao_do_schema(self, db: SqliteDatabase) -> None:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 6

    def test_indices_criados_depois_das_migracoes(self, db: SqliteDatabase) -> None:
        """A ordem importa: os índices referenciam colunas que só existem depois
        de migrar. Criá-los antes falhava com 'no such column' num banco antigo
        e o app não abria — foi um bug real."""
        indexes = {
            r[0]
            for r in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

        assert "idx_laps_track_time" in indexes
        assert "idx_laps_session" in indexes

    def test_migracao_de_banco_v5(self, tmp_path) -> None:
        """Um banco na v5 (sem sessões) tem de migrar sem perder dado."""
        path = tmp_path / "antigo.db"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
            CREATE TABLE cars (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
            CREATE TABLE laps (id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER, car_id INTEGER, is_player INTEGER NOT NULL DEFAULT 1,
                lap_time_ms INTEGER NOT NULL, recorded_at REAL NOT NULL,
                frame_count INTEGER NOT NULL);
            INSERT INTO tracks (name, created_at) VALUES ('Suzuka', 1000);
            INSERT INTO laps (track_id, lap_time_ms, recorded_at, frame_count)
                VALUES (1, 95000, 1000, 10);
            PRAGMA user_version = 5;
        """)
        old.commit()
        old.close()

        migrated = SqliteDatabase(path)

        assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 6
        # O dado antigo sobreviveu.
        assert migrated.connection.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 1
        # E a coluna nova existe, nula para as voltas de antes.
        assert (
            migrated.connection.execute("SELECT session_id FROM laps").fetchone()[0]
            is None
        )
        migrated.close()


class TestGravacaoDeVolta:
    def test_grava_e_recupera(self, laps: SqliteLapRepository, db: SqliteDatabase) -> None:
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        lap_id = laps.save(make_lap(track_id, 95_000))

        restored = laps.get_by_id(lap_id)

        assert restored is not None
        assert restored.lap_time_ms == 95_000
        assert len(restored.points) == 50

    def test_amostras_voltam_identicas(
        self, laps: SqliteLapRepository, db: SqliteDatabase
    ) -> None:
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        original = make_points(30)
        lap = Lap(track_id=track_id, lap_time_ms=90_000, points=original)

        lap_id = laps.save(lap)

        assert laps.load_points(lap_id) == original

    def test_setores_sao_calculados(
        self, laps: SqliteLapRepository, db: SqliteDatabase
    ) -> None:
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        lap_id = laps.save(make_lap(track_id, 95_000))

        sectors = laps.get_sector_times(lap_id)

        assert len(sectors) == 3
        assert all(s is not None and s >= 0 for s in sectors)

    def test_transacao_unica_reverte_tudo(
        self, laps: SqliteLapRepository, db: SqliteDatabase
    ) -> None:
        """O bug que a transação única resolve: falha no meio deixava volta
        gravada sem setores, e o histórico exibia isso sem erro nenhum."""
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        # car_id inexistente viola a foreign key no meio do INSERT.
        bad = Lap(track_id=track_id, car_id=99999, lap_time_ms=90_000,
                  points=make_points(20))

        with pytest.raises(sqlite3.IntegrityError):
            laps.save(bad)

        assert db.connection.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM lap_frames").fetchone()[0] == 0

    def test_batch_de_setores_evita_n_mais_1(
        self, laps: SqliteLapRepository, db: SqliteDatabase
    ) -> None:
        """A tela de histórico consultava setor a setor dentro do laço: 50
        voltas viravam 51 consultas."""
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        lap_ids = [laps.save(make_lap(track_id, 90_000 + i * 100)) for i in range(5)]

        batch = laps.get_sector_times_batch(lap_ids)

        assert set(batch) == set(lap_ids)
        assert all(len(v) == 3 for v in batch.values())

    def test_batch_vazio_nao_consulta(self, laps: SqliteLapRepository) -> None:
        assert laps.get_sector_times_batch([]) == {}


class TestRetencao:
    """P8 — decisão do usuário: histórico limitado por pista (20 recentes)."""

    def test_mantem_as_n_mais_recentes(self, db: SqliteDatabase) -> None:
        repo = SqliteLapRepository(db, keep_recent_per_track=5, keep_best_per_track=0)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        for i in range(12):
            lap = make_lap(track_id, 100_000 - i * 10)
            lap.start_time = datetime.fromtimestamp(1_700_000_000 + i * 60)
            repo.save(lap)

        assert len(repo.get_by_track(track_id)) == 5

    def test_mantem_tambem_as_melhores(self, db: SqliteDatabase) -> None:
        """As melhores sobrevivem mesmo saindo da janela de recência — é o que
        impede a retenção de apagar um recorde."""
        repo = SqliteLapRepository(db, keep_recent_per_track=3, keep_best_per_track=2)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        # A primeira volta é a mais rápida e a mais antiga.
        record = make_lap(track_id, 80_000)
        record.start_time = datetime.fromtimestamp(1_700_000_000)
        repo.save(record)

        for i in range(1, 10):
            slower = make_lap(track_id, 100_000 + i)
            slower.start_time = datetime.fromtimestamp(1_700_000_000 + i * 60)
            repo.save(slower)

        best = repo.get_best(track_id)
        assert best is not None
        assert best.lap_time_ms == 80_000, "o recorde não pode ser apagado"

    def test_zero_desliga_a_retencao(self, db: SqliteDatabase) -> None:
        """Quem quiser histórico ilimitado configura 0 nos dois."""
        repo = SqliteLapRepository(db, keep_recent_per_track=0, keep_best_per_track=0)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        for i in range(30):
            repo.save(make_lap(track_id, 90_000 + i))

        assert len(repo.get_by_track(track_id)) == 30

    def test_retencao_e_por_pista(self, db: SqliteDatabase) -> None:
        """Apagar em Suzuka não pode mexer nas voltas de Interlagos."""
        repo = SqliteLapRepository(db, keep_recent_per_track=2, keep_best_per_track=0)
        tracks = SqliteTrackRepository(db)
        suzuka = tracks.get_or_create("Suzuka")
        interlagos = tracks.get_or_create("Interlagos")

        for i in range(5):
            repo.save(make_lap(interlagos, 90_000 + i))
        for i in range(5):
            repo.save(make_lap(suzuka, 95_000 + i))

        assert len(repo.get_by_track(interlagos)) == 2
        assert len(repo.get_by_track(suzuka)) == 2

    def test_voltas_de_replay_nao_contam_como_recorde(self, db: SqliteDatabase) -> None:
        """Volta de IA/replay não pode virar o recorde do piloto."""
        repo = SqliteLapRepository(db, keep_recent_per_track=10, keep_best_per_track=5)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        ai_lap = make_lap(track_id, 70_000)
        ai_lap.is_player = False
        repo.save(ai_lap)
        repo.save(make_lap(track_id, 95_000))

        best = repo.get_best(track_id)
        assert best is not None
        assert best.lap_time_ms == 95_000


class TestSessoes:
    """P9 — sessões não eram persistidas."""

    def test_inicia_e_encerra(self, db: SqliteDatabase) -> None:
        repo = SqliteSessionRepository(db)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")

        session_id = repo.start(track_id, None)
        repo.finish(session_id, lap_count=7)

        session = repo.get_by_id(session_id)
        assert session is not None
        assert session.is_active is False
        assert session.duration_s is not None

    def test_sessao_em_andamento_fica_ativa(self, db: SqliteDatabase) -> None:
        repo = SqliteSessionRepository(db)
        session_id = repo.start(None, None)

        session = repo.get_by_id(session_id)
        assert session is not None
        assert session.is_active is True

    def test_encontra_sessoes_nao_encerradas(self, db: SqliteDatabase) -> None:
        """§8: recuperação após falha. Uma sessão sem `ended_at` significa que o
        app caiu ou foi morto no meio."""
        repo = SqliteSessionRepository(db)
        crashed = repo.start(None, None)
        finished = repo.start(None, None)
        repo.finish(finished, 3)

        unfinished = repo.find_unfinished()

        assert [s.id for s in unfinished] == [crashed]

    def test_voltas_ligam_se_a_sessao(
        self, db: SqliteDatabase, laps: SqliteLapRepository
    ) -> None:
        sessions = SqliteSessionRepository(db)
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        session_id = sessions.start(track_id, None)

        for i in range(3):
            lap = make_lap(track_id, 90_000 + i)
            lap.session_id = session_id
            laps.save(lap)

        assert len(laps.get_by_session(session_id)) == 3


class TestCatalogos:
    def test_pista_e_idempotente(self, db: SqliteDatabase) -> None:
        repo = SqliteTrackRepository(db)

        assert repo.get_or_create("Suzuka") == repo.get_or_create("Suzuka")

    def test_pista_ignora_espaco_em_volta(self, db: SqliteDatabase) -> None:
        repo = SqliteTrackRepository(db)

        assert repo.get_or_create("  Suzuka  ") == repo.get_or_create("Suzuka")

    def test_pista_vazia_e_recusada(self, db: SqliteDatabase) -> None:
        """Não existe pista-padrão: um nome inventado misturaria voltas de
        circuitos diferentes no mesmo histórico."""
        with pytest.raises(ValueError, match="vazio"):
            SqliteTrackRepository(db).get_or_create("   ")

    def test_carro_sem_nome_vira_desconhecido(self, db: SqliteDatabase) -> None:
        """Ao contrário da pista, carro vazio é escolha válida."""
        repo = SqliteCarRepository(db)
        car_id = repo.get_or_create("")
        car = repo.get_by_id(car_id)

        assert car is not None
        assert car.name == "Desconhecido"


class TestSetoresPorDistancia:
    def test_divide_em_tres(self, db: SqliteDatabase, laps: SqliteLapRepository) -> None:
        track_id = SqliteTrackRepository(db).get_or_create("Suzuka")
        lap_id = laps.save(make_lap(track_id, 90_000, points=90))

        sectors = compute_sector_times(db.connection, lap_id, 3, track_id=track_id)

        assert len(sectors) == 3
        assert sum(sectors) > 0

    def test_volta_sem_amostras_da_lista_vazia(self, db: SqliteDatabase) -> None:
        assert compute_sector_times(db.connection, 99999, 3) == []


class TestPipelineCompleto:
    def test_telemetria_sintetica_vira_volta_gravada(self, db: SqliteDatabase) -> None:
        """De ponta a ponta: quadros → motor → volta → banco → leitura."""
        from gt7core.session.manager import LapSaved, RecordingService, SessionManager
        from gt7core.telemetry.sources.mock import synthetic_session

        bus = EventBus()
        engine = TelemetryEngine(bus)
        lap_repo = SqliteLapRepository(db)
        session_repo = SqliteSessionRepository(db)
        manager = SessionManager(bus, session_repo)
        RecordingService(bus, lap_repo, manager)

        track_id = SqliteTrackRepository(db).get_or_create("Circuito Sintético")
        manager.set_track(Track(id=track_id, name="Circuito Sintético"))
        manager.start_session()

        saved: list[LapSaved] = []
        bus.subscribe(LapSaved, saved.append)

        for frame in synthetic_session(lap_count=3):
            engine.on_frame(frame)
        manager.end_session()

        assert len(saved) == 2  # a última só fecha quando o contador vira
        assert all(event.lap_id > 0 for event in saved)

        stored = lap_repo.get_by_track(track_id)
        assert len(stored) == 2
        assert all(lap.lap_time_ms > 0 for lap in stored)

        # As amostras sobreviveram à ida e volta pelo banco.
        full = lap_repo.get_by_id(stored[0].id)  # type: ignore[arg-type]
        assert full is not None
        assert len(full.points) > 5000

    def test_volta_sem_pista_e_descartada(self, db: SqliteDatabase) -> None:
        """Sem pista não há onde arquivar — e inventar uma corromperia o
        histórico de todos os circuitos."""
        from gt7core.session.manager import LapDiscarded, RecordingService, SessionManager

        bus = EventBus()
        engine = TelemetryEngine(bus)
        manager = SessionManager(bus, SqliteSessionRepository(db))
        RecordingService(bus, SqliteLapRepository(db), manager)
        manager.start_session()

        discarded: list[LapDiscarded] = []
        bus.subscribe(LapDiscarded, discarded.append)

        for frame in synthetic_lap(lap_number=1, lap_time_ms=2_000):
            engine.on_frame(frame)
        for frame in synthetic_lap(lap_number=2, lap_time_ms=2_000, last_lap_ms=2_000):
            engine.on_frame(frame)

        assert len(discarded) == 1
        assert discarded[0].reason == "nenhuma pista definida"
        assert db.connection.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 0

    def test_falha_de_gravacao_vira_evento_visivel(self, db: SqliteDatabase) -> None:
        """A versão anterior engolia isso num print(): o piloto via tudo normal
        na tela e só descobria a perda quando o histórico vinha vazio."""
        from gt7core.domain.models import Track
        from gt7core.session.manager import (
            LapSaveFailed,
            RecordingService,
            SessionManager,
        )

        bus = EventBus()
        engine = TelemetryEngine(bus)
        manager = SessionManager(bus, SqliteSessionRepository(db))
        RecordingService(bus, SqliteLapRepository(db), manager)

        manager.set_track(Track(id=SqliteTrackRepository(db).get_or_create("X"), name="X"))
        manager.start_session()

        # Só depois de a sessão existir: um car_id inexistente viola a foreign
        # key, e queremos que a falha aconteça na gravação da volta — não na
        # abertura da sessão, que também referencia `cars`.
        manager.set_car(Car(id=99999, name="Fantasma"))

        failures: list[LapSaveFailed] = []
        bus.subscribe(LapSaveFailed, failures.append)

        for frame in synthetic_lap(lap_number=1, lap_time_ms=2_000):
            engine.on_frame(frame)
        for frame in synthetic_lap(lap_number=2, lap_time_ms=2_000, last_lap_ms=2_000):
            engine.on_frame(frame)

        assert len(failures) == 1
        assert "Falha ao salvar" in failures[0].message
