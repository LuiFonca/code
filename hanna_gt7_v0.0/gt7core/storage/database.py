"""
Conexão e schema do banco — portado sem Qt, com sessões e retenção configurável.

Baseado em `src/infrastructure/repositories/sqlite_database.py`, que a auditoria
classificou como "estender, não reescrever": as migrações incrementais por
`PRAGMA user_version`, o caminho injetado (que permite `:memory:` nos testes) e
a ordem índices-depois-de-migrações estavam todos corretos.

Duas mudanças, ambas resolvendo achados da auditoria:

**P9 — sessões não eram persistidas.** O modelo `Session` existia mas morria com
o processo, o que tornava "recuperar sessão após falha" (§8) impossível. A
migração v6 cria a tabela e liga as voltas a ela.

**P8 — retenção era constante enterrada no módulo.** `KEEP_RECENT_PER_TRACK` e
`KEEP_BEST_PER_TRACK` eram literais aqui dentro, e toda gravação apagava o que
sobrasse sem aviso. Agora vêm da configuração (padrão: 20 recentes + 5 melhores
por pista) e `0` desliga a exclusão automática.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

from ..observability.logging import get_logger

_log = get_logger(__name__)

SCHEMA_VERSION = 6

UNKNOWN_CAR_NAME = "Desconhecido"
UNKNOWN_TRACK_NAME = "Pista não identificada"


class SqliteDatabase:
    """Dona da conexão e do schema. Os repositórios recebem esta instância."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()

        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False: a conexão é compartilhada entre a thread da
        # interface e tarefas de fundo. As escritas são serializadas pelo lock
        # abaixo; as leituras o próprio SQLite trata.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")

        self._create_schema()
        self._run_migrations()
        # Depois das migrações, nunca antes: os índices referenciam colunas que
        # só existem a partir de versões posteriores. Num banco antigo, criá-los
        # antes falha com "no such column" e o app não abre — foi um bug real.
        self._create_indexes()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        """Protege blocos de escrita. Os repositórios usam com `with`."""
        return self._lock

    def close(self) -> None:
        self._conn.close()

    # ---------- schema ----------

    def _create_schema(self) -> None:
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER REFERENCES tracks(id),
                car_id INTEGER REFERENCES cars(id),
                started_at REAL NOT NULL,
                ended_at REAL,
                lap_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS laps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
                track_id INTEGER,
                car_id INTEGER REFERENCES cars(id),
                is_player INTEGER NOT NULL DEFAULT 1,
                lap_time_ms INTEGER NOT NULL,
                recorded_at REAL NOT NULL,
                frame_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lap_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                elapsed_ms INTEGER NOT NULL,
                distance_m REAL NOT NULL,
                speed_kmh REAL NOT NULL,
                rpm REAL NOT NULL,
                gear INTEGER NOT NULL,
                throttle REAL NOT NULL,
                brake REAL NOT NULL,
                fuel_level REAL,
                tire_temp_fl REAL, tire_temp_fr REAL,
                tire_temp_rl REAL, tire_temp_rr REAL,
                position_x REAL, position_z REAL,
                g_lateral REAL, g_longitudinal REAL,
                suspension_fl REAL, suspension_fr REAL,
                suspension_rl REAL, suspension_rr REAL,
                tire_slip_fl REAL, tire_slip_fr REAL,
                tire_slip_rl REAL, tire_slip_rr REAL,
                turbo_boost REAL, oil_temp REAL, water_temp REAL
            );
            CREATE TABLE IF NOT EXISTS sector_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
                sector_index INTEGER NOT NULL,
                time_ms INTEGER NOT NULL
            );
        """)
        conn.commit()

    def _create_indexes(self) -> None:
        """Índices das consultas quentes: melhor volta da pista, listagem por
        recência, carga das amostras e voltas de uma sessão."""
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_laps_track_time
                ON laps(track_id, is_player, lap_time_ms);
            CREATE INDEX IF NOT EXISTS idx_laps_track_recent
                ON laps(track_id, is_player, recorded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_laps_session
                ON laps(session_id);
            CREATE INDEX IF NOT EXISTS idx_frames_lap_seq
                ON lap_frames(lap_id, seq);
            CREATE INDEX IF NOT EXISTS idx_sectors_lap
                ON sector_times(lap_id, sector_index);
        """)
        self._conn.commit()

    def _run_migrations(self) -> None:
        """Migrações incrementais guiadas por `PRAGMA user_version`.

        Bancos criados do zero já nascem no schema atual — estas migrações
        existem para bancos de usuários vindos de versões anteriores. Cada
        `ALTER TABLE` é tolerante a "coluna já existe" porque a mesma coluna
        pode ter vindo tanto da criação quanto da migração.
        """
        conn = self._conn
        current = conn.execute("PRAGMA user_version").fetchone()[0]

        if current and current < 6:
            # 5 -> 6: sessões passam a ser persistidas (P9). As voltas antigas
            # ficam com session_id NULL — pertencem a sessões que nunca foram
            # gravadas, e inventar uma sessão sintética para elas seria fabricar
            # dado que não existiu.
            _log.info("migrando banco", extra={"from": current, "to": 6})
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER REFERENCES tracks(id),
                    car_id INTEGER REFERENCES cars(id),
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    lap_count INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._try_alter("laps", "session_id INTEGER REFERENCES sessions(id)")

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _try_alter(self, table: str, column_def: str) -> None:
        """ADD COLUMN tolerante a coluna já existente."""
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def reference_lap_distance(
    conn: sqlite3.Connection, track_id: int | None, exclude_lap_id: int | None = None
) -> float | None:
    """Distância 'canônica' da pista, para ancorar os limites de setor.

    O GT7 não expõe os pontos oficiais de setor. Usar a distância total de cada
    volta isoladamente faria o "setor 2" cair num ponto físico diferente a cada
    volta — uma volta mais longa desloca todos os limites. A mediana das últimas
    voltas dá uma referência estável; só a primeira volta de uma pista define os
    próprios limites, por não haver com o que comparar.
    """
    if track_id is None:
        return None

    query = "SELECT id FROM laps WHERE track_id = ? AND is_player = 1"
    params: list[object] = [track_id]
    if exclude_lap_id is not None:
        query += " AND id != ?"
        params.append(exclude_lap_id)
    query += " ORDER BY recorded_at DESC LIMIT 10"

    lap_ids = [r[0] for r in conn.execute(query, params).fetchall()]
    if not lap_ids:
        return None

    totals = []
    for other_lap_id in lap_ids:
        row = conn.execute(
            "SELECT MAX(distance_m) FROM lap_frames WHERE lap_id = ?", (other_lap_id,)
        ).fetchone()
        if row and row[0] and row[0] > 50:
            totals.append(row[0])

    if not totals:
        return None
    totals.sort()
    return float(totals[len(totals) // 2])


def compute_sector_times(
    conn: sqlite3.Connection,
    lap_id: int,
    num_sectors: int,
    track_id: int | None = None,
) -> list[int]:
    """Divide a volta em setores por **distância**, não por tempo.

    Limitação conhecida e documentada: sem os pontos oficiais de setor da pista,
    a volta é dividida em trechos de distância igual, ancorados na distância de
    referência. É aproximado, mas suficiente para localizar em que parte da volta
    houve ganho ou perda. A detecção de curvas da Fase 6 substituirá isto.
    """
    rows = conn.execute(
        "SELECT elapsed_ms, distance_m FROM lap_frames WHERE lap_id = ? ORDER BY seq ASC",
        (lap_id,),
    ).fetchall()
    if not rows:
        return []

    lap_total_distance = rows[-1][1]
    if lap_total_distance <= 0:
        return []

    reference = reference_lap_distance(conn, track_id, exclude_lap_id=lap_id)
    # Referência absurdamente menor que esta volta significa histórico ruim
    # (voltas parciais ou abortadas); melhor cair para a distância da própria.
    if reference and reference < lap_total_distance * 0.3:
        reference = None
    total_distance = reference or lap_total_distance

    boundaries = [total_distance * (i / num_sectors) for i in range(1, num_sectors + 1)]

    sector_times: list[int] = []
    last_boundary_ms = rows[0][0]
    boundary_index = 0

    for elapsed_ms, distance_m in rows:
        if boundary_index >= num_sectors:
            break
        if distance_m >= boundaries[boundary_index]:
            sector_times.append(elapsed_ms - last_boundary_ms)
            last_boundary_ms = elapsed_ms
            boundary_index += 1

    if len(sector_times) < num_sectors:
        sector_times.append(rows[-1][0] - last_boundary_ms)

    return sector_times
