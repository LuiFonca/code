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

Uma conexão por thread
----------------------
A versão anterior compartilhava **uma** conexão entre todas as threads, com
`check_same_thread=False` e um lock aplicado só às escritas. O comentário dizia
que "as leituras o próprio SQLite trata" — e isso é verdade do SQLite, mas não
do objeto `sqlite3.Connection` do Python, que não é feito para uso concorrente.

O sintoma não foi exceção: foi **segmentation fault**, na Fase 6, quando um
assinante passou a ler da thread de captura enquanto a interface lia da sua. Um
processo morrendo no meio de uma sessão, sem traceback, é o pior modo de falha
possível para quem está gravando dados que não voltam.

Agora cada thread abre a sua conexão, que é o modelo que o `sqlite3` documenta
como seguro. Junto vem o **WAL**: sem ele, um escritor bloquearia todos os
leitores, e trocar um segfault raro por uma interface que trava durante a
gravação da volta seria um mau negócio.
"""

from __future__ import annotations

import contextlib
import itertools
import sqlite3
import threading
from pathlib import Path

from ..analytics.sectors import resolve_anchor, sector_boundaries
from ..observability.logging import get_logger

_log = get_logger(__name__)

SCHEMA_VERSION = 9

_memory_counter = itertools.count()
_memory_lock = threading.Lock()


def _next_memory_id() -> int:
    """Nome único para cada banco em memória.

    Era `id(self)`, e **o CPython reaproveita `id()`**: um objeto liberado cede
    o endereço ao próximo, e o banco novo herdava o nome do antigo. Com cache
    compartilhado isso significa herdar os *dados* do antigo se alguma conexão
    dele ainda estiver aberta — voltas de um teste aparecendo noutro, de forma
    intermitente e sem nada em comum entre os dois.

    Um contador nunca reaproveita, e é o que torna cada banco em memória
    genuinamente privado.
    """
    with _memory_lock:
        return next(_memory_counter)


UNKNOWN_CAR_NAME = "Desconhecido"
UNKNOWN_TRACK_NAME = "Pista não identificada"


class SqliteDatabase:
    """Dona da conexão e do schema. Os repositórios recebem esta instância."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        raw_path = str(db_path)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._closed = False

        if raw_path == ":memory:":
            # Um banco em memória privado morre com a conexão que o abriu, o que
            # tornaria "uma conexão por thread" um banco por thread. A URI de
            # cache compartilhado dá o comportamento esperado: mesma base, várias
            # conexões. O nome é único por instância para dois `SqliteDatabase`
            # em memória não se enxergarem — que é o que os testes assumem.
            self._db_path = f"file:gt7mem{_next_memory_id()}?mode=memory&cache=shared"
            self._uri = True
            # Uma conexão âncora mantida aberta: com cache compartilhado, o banco
            # é destruído quando a **última** conexão fecha, e sem esta o schema
            # sumiria entre duas threads.
            self._anchor: sqlite3.Connection | None = self._open()
        else:
            self._db_path = raw_path
            self._uri = False
            self._anchor = None
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._create_schema()
        self._run_migrations()
        # Depois das migrações, nunca antes: os índices referenciam colunas que
        # só existem a partir de versões posteriores. Num banco antigo, criá-los
        # antes falha com "no such column" e o app não abre — foi um bug real.
        self._create_indexes()

    def _open(self) -> sqlite3.Connection:
        """Abre uma conexão nova, já com os PRAGMAs desta aplicação."""
        conn = sqlite3.connect(self._db_path, uri=self._uri, timeout=10.0)
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL: leitores não bloqueiam o escritor nem vice-versa. É o que permite
        # a interface continuar consultando o histórico durante os ~40 ms em que
        # a volta está sendo gravada.
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL em vez de FULL: com WAL, a diferença é entre perder ou não os
        # últimos commits num corte de energia do sistema **operacional** — não
        # numa queda do programa. Para telemetria de jogo é uma troca óbvia.
        conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._all.append(conn)
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        """A conexão **desta** thread, aberta sob demanda.

        Os repositórios já acessavam o banco por esta propriedade, então passar
        de compartilhada para local por thread não exigiu tocar em nenhum deles.
        """
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            if self._closed:
                raise sqlite3.ProgrammingError("banco já fechado")
            conn = self._open()
            self._local.conn = conn
        return conn

    @property
    def lock(self) -> threading.RLock:
        """Serializa blocos de escrita.

        Continua existindo mesmo com uma conexão por thread: o WAL admite um
        escritor por vez, e sem o lock duas gravações concorrentes viram
        `database is locked` depois do timeout. Com ele, a segunda espera.
        """
        return self._lock

    def close(self) -> None:
        """Fecha todas as conexões abertas, de todas as threads."""
        with self._lock:
            self._closed = True
            connections, self._all = list(self._all), []
        for conn in connections:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        self._local = threading.local()
        self._anchor = None

    # ---------- schema ----------

    def _create_schema(self) -> None:
        conn = self.connection
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                length_m REAL
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
                frame_count INTEGER NOT NULL,
                sector_anchor_m REAL
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
                position_x REAL, position_z REAL, position_y REAL,
                road_plane_x REAL, road_plane_y REAL, road_plane_z REAL,
                g_lateral REAL, g_longitudinal REAL,
                suspension_fl REAL, suspension_fr REAL,
                suspension_rl REAL, suspension_rr REAL,
                tire_slip_fl REAL, tire_slip_fr REAL,
                tire_slip_rl REAL, tire_slip_rr REAL,
                turbo_boost REAL, oil_temp REAL, water_temp REAL,
                flags INTEGER
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
        self.connection.executescript("""
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
        self.connection.commit()

    def _run_migrations(self) -> None:
        """Migrações incrementais guiadas por `PRAGMA user_version`.

        Bancos criados do zero já nascem no schema atual — estas migrações
        existem para bancos de usuários vindos de versões anteriores. Cada
        `ALTER TABLE` é tolerante a "coluna já existe" porque a mesma coluna
        pode ter vindo tanto da criação quanto da migração.
        """
        conn = self.connection
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

        if current and current < 7:
            # 6 -> 7: o bitfield de estado do pacote passa a ser gravado por
            # amostra, que é o que permite mostrar TCS e ASM atuando numa volta
            # já guardada. Voltas antigas ficam com NULL — "não foi gravado", e
            # não "não atuou". A distinção importa: 0 faria uma volta antiga
            # alegar pilotagem sem auxílio que ninguém mediu.
            _log.info("migrando banco", extra={"from": current, "to": 7})
            self._try_alter("lap_frames", "flags INTEGER")

        if current and current < 8:
            # 7 -> 8: a altitude e a normal do asfalto passam a ser gravadas.
            # O pacote sempre trouxe as duas; o programa é que as descartava na
            # fronteira do domínio, e sem elas uma freada em descida ficava
            # indistinguível de uma freada no plano.
            #
            # Voltas antigas ficam com NULL nas quatro colunas — "não foi
            # medido". Preencher com zero afirmaria pista horizontal, que é uma
            # medição que ninguém fez, e a força G corrigida sairia errada com
            # cara de certa em toda a base histórica.
            _log.info("migrando banco", extra={"from": current, "to": 8})
            for coluna in (
                "position_y REAL",
                "road_plane_x REAL",
                "road_plane_y REAL",
                "road_plane_z REAL",
            ):
                self._try_alter("lap_frames", coluna)

        if current and current < 9:
            # 8 -> 9: o comprimento oficial da pista passa a ser gravado, e cada
            # volta registra a âncora com que seus setores foram medidos.
            #
            # O comprimento vem do catálogo do jogo (`track_list.csv`, 105 de
            # 105 pistas), e é o que finalmente dá um ponto **físico e fixo**
            # para as divisas de setor: Suzuka tem 5.807 m hoje e vai ter 5.807
            # m daqui a um ano. Antes disso a divisa era ancorada na mediana das
            # últimas voltas, na melhor volta ou na própria volta — três pontos
            # diferentes no mesmo programa.
            #
            # `sector_anchor_m` guarda **com que âncora** os setores gravados
            # foram calculados. Sem esse registro não há como saber se um valor
            # guardado ainda vale, e a única saída segura seria recalcular tudo
            # a cada abertura — que é exatamente o custo que se quer eliminar.
            #
            # Pistas e voltas antigas ficam com NULL: "não se sabe", e não um
            # comprimento inventado.
            _log.info("migrando banco", extra={"from": current, "to": 9})
            self._try_alter("tracks", "length_m REAL")
            self._try_alter("laps", "sector_anchor_m REAL")

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _try_alter(self, table: str, column_def: str) -> None:
        """ADD COLUMN tolerante a coluna já existente."""
        with contextlib.suppress(sqlite3.OperationalError):
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


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


def track_length(conn: sqlite3.Connection, track_id: int | None) -> float | None:
    """Comprimento oficial da pista, quando o catálogo o identificou."""
    if track_id is None:
        return None
    row = conn.execute(
        "SELECT length_m FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def sector_anchor_for(
    conn: sqlite3.Connection, track_id: int | None, exclude_lap_id: int | None = None
) -> float | None:
    """A âncora de setor desta pista: catálogo, e só então mediana das recentes.

    A escolha mora em `analytics.sectors`; aqui só se juntam as duas fontes que
    dependem do banco.
    """
    return resolve_anchor(
        track_length(conn, track_id),
        reference_lap_distance(conn, track_id, exclude_lap_id=exclude_lap_id),
    )


def compute_sector_times(
    conn: sqlite3.Connection,
    lap_id: int,
    num_sectors: int,
    track_id: int | None = None,
    anchor_m: float | None = None,
) -> list[int]:
    """Divide a volta em setores por **distância**, não por tempo.

    Limitação conhecida: sem os pontos oficiais de setor da pista, a volta é
    dividida em trechos de distância igual. É aproximado, mas suficiente para
    localizar em que parte da volta houve ganho ou perda.

    O que deixou de ser aproximado é **onde** ficam os cortes: as divisas saem do
    comprimento oficial do catálogo, o mesmo ponto físico em toda volta da pista.
    `anchor_m` já resolvido evita reconsultar o banco por volta; sem ele, resolve
    aqui.
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

    if anchor_m is None:
        anchor_m = sector_anchor_for(conn, track_id, exclude_lap_id=lap_id)

    boundaries = sector_boundaries(lap_total_distance, anchor_m, num_sectors)

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
