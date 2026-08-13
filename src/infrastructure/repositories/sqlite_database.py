"""
Conexão e schema do banco SQLite.

Extraído para uma classe própria porque os três repositórios SQLite (voltas,
carros, pistas) compartilham o mesmo arquivo: sem isso, cada um replicaria
criação de tabela e migração, e a ordem em que fossem instanciados passaria a
importar.

Diferenças em relação ao módulo antigo (`analysis/lap_storage.py`):

- **Caminho injetado.** Era uma constante global (`DB_PATH = Path.home()/...`),
  o que tornava impossível testar sem escrever no banco real do usuário. Agora
  vem pelo construtor e aceita `":memory:"`.
- **Conexão persistente.** Antes cada função abria e fechava a própria conexão.
  Além do custo por chamada, isso inviabiliza `:memory:` — fechar a conexão
  destrói o banco. Aqui a conexão vive junto com o objeto.
- **Escritas serializadas.** Com conexão compartilhada e `check_same_thread=False`,
  um lock protege as escritas.
"""

import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".hanna_gt7_ai" / "laps.db"

# Incrementar sempre que uma coluna/tabela nova for necessária, adicionando a
# migração correspondente em `_run_migrations`. Sem isso, o app quebra em
# silêncio no banco de quem já usava a versão anterior.
SCHEMA_VERSION = 6

# Retenção por pista: as N mais rápidas (recordes) + as N mais recentes
# (histórico cronológico). O resto é descartado ao salvar, para o banco não
# crescer sem limite. São apenas os **padrões** — o repositório aceita outros
# valores pelo construtor, porque quem treina a mesma pista todo dia estoura
# 50 voltas rápido.
KEEP_BEST_PER_TRACK = 5
KEEP_RECENT_PER_TRACK = 50

UNKNOWN_CAR_NAME = "Desconhecido"
UNKNOWN_TRACK_NAME = "Pista não identificada"


class SqliteDatabase:
    """Dona da conexão e do schema. Os repositórios recebem esta instância."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()

        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False: a conexão é compartilhada entre a thread da
        # UI e eventuais tarefas de fundo. As escritas são serializadas pelo
        # lock abaixo; as leituras o SQLite já trata.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")

        self._create_schema()
        self._run_migrations()
        # Rede de segurança: as migrações são disparadas pelo carimbo de versão,
        # então um banco cujo `user_version` não corresponde ao conteúdo real
        # (backup restaurado pela metade, carimbo manual, migração interrompida)
        # passaria batido e só quebraria em tempo de execução, com um erro de
        # SQL incompreensível para o usuário. Conferir as colunas é barato e
        # transforma esse cenário em nada.
        self._ensure_columns()
        # Depois das migrações, nunca antes: os índices referenciam colunas
        # (is_player) que só existem a partir da v4. Num banco de usuário ainda
        # na v3, criá-los antes falha com "no such column" e o app não abre.
        self._create_indexes()

    def _ensure_columns(self) -> None:
        """Garante que toda coluna esperada exista, independente da versão."""
        expected_frames = {
            "elapsed_ms": "INTEGER NOT NULL DEFAULT 0",
            "distance_m": "REAL", "speed_kmh": "REAL", "rpm": "REAL",
            "gear": "INTEGER", "throttle": "REAL", "brake": "REAL",
            "fuel_level": "REAL",
            "tire_temp_fl": "REAL", "tire_temp_fr": "REAL",
            "tire_temp_rl": "REAL", "tire_temp_rr": "REAL",
            "position_x": "REAL", "position_z": "REAL",
            "g_lateral": "REAL", "g_longitudinal": "REAL",
            "suspension_fl": "REAL", "suspension_fr": "REAL",
            "suspension_rl": "REAL", "suspension_rr": "REAL",
            "tire_slip_fl": "REAL", "tire_slip_fr": "REAL",
            "tire_slip_rl": "REAL", "tire_slip_rr": "REAL",
            "turbo_boost": "REAL", "oil_temp": "REAL", "water_temp": "REAL",
        }
        existing = {
            r[1] for r in self._conn.execute("PRAGMA table_info(lap_frames)").fetchall()
        }
        for name, decl in expected_frames.items():
            if name not in existing:
                self._try_alter("lap_frames", f"{name} {decl}")

        expected_laps = {
            "car_id": "INTEGER REFERENCES cars(id)",
            "is_player": "INTEGER NOT NULL DEFAULT 1",
            "is_complete": "INTEGER NOT NULL DEFAULT 1",
        }
        existing = {
            r[1] for r in self._conn.execute("PRAGMA table_info(laps)").fetchall()
        }
        for name, decl in expected_laps.items():
            if name not in existing:
                self._try_alter("laps", f"{name} {decl}")

        existing = {
            r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        if "sector_fractions" not in existing:
            self._try_alter("tracks", "sector_fractions TEXT")
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.Lock:
        """Protege blocos de escrita. Os repositórios usam com `with`."""
        return self._lock

    def close(self) -> None:
        self._conn.close()

    # ---------- schema ----------

    def _create_schema(self) -> None:
        conn = self._conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                sector_fractions TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS laps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER REFERENCES tracks(id),
                car_id INTEGER REFERENCES cars(id),
                is_player INTEGER NOT NULL DEFAULT 1,
                is_complete INTEGER NOT NULL DEFAULT 1,
                lap_time_ms INTEGER NOT NULL,
                recorded_at REAL NOT NULL,
                frame_count INTEGER NOT NULL
            )
        """)
        conn.execute("""
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
                tire_temp_fl REAL,
                tire_temp_fr REAL,
                tire_temp_rl REAL,
                tire_temp_rr REAL,
                position_x REAL,
                position_z REAL,
                g_lateral REAL,
                g_longitudinal REAL,
                suspension_fl REAL,
                suspension_fr REAL,
                suspension_rl REAL,
                suspension_rr REAL,
                tire_slip_fl REAL,
                tire_slip_fr REAL,
                tire_slip_rl REAL,
                tire_slip_rr REAL,
                turbo_boost REAL,
                oil_temp REAL,
                water_temp REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sector_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
                sector_index INTEGER NOT NULL,
                time_ms INTEGER NOT NULL
            )
        """)
        conn.commit()

    def _create_indexes(self) -> None:
        """Índices das consultas quentes: melhor volta da pista, listagem por
        recência e carga das amostras de uma volta.

        Roda depois das migrações — ver o comentário no construtor.
        """
        conn = self._conn
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_laps_track_time "
            "ON laps(track_id, is_player, lap_time_ms)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_laps_track_recent "
            "ON laps(track_id, is_player, recorded_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_lap_seq ON lap_frames(lap_id, seq)"
        )
        conn.commit()

    def _run_migrations(self) -> None:
        """Migrações incrementais guiadas por `PRAGMA user_version`.

        Bancos criados do zero já nascem no schema atual — as migrações abaixo
        existem para bancos de usuários que vêm de versões anteriores. Cada
        `ALTER TABLE` é tolerante a "coluna já existe" porque a mesma coluna
        pode ter vindo tanto da criação quanto da migração.
        """
        conn = self._conn
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]

        if current_version < 2:
            self._try_alter("lap_frames", "elapsed_ms INTEGER NOT NULL DEFAULT 0")

        if current_version < 3:
            # 2 -> 3: introduz a tabela de pistas e campos por amostra. O antigo
            # `track_key` (texto livre na própria volta) vira linha em `tracks`.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                )
            """)
            self._try_alter("laps", "track_id INTEGER REFERENCES tracks(id)")

            existing = [r[1] for r in conn.execute("PRAGMA table_info(laps)").fetchall()]
            if "track_key" in existing:
                old_keys = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT track_key FROM laps WHERE track_key IS NOT NULL"
                    ).fetchall()
                ]
                for key in old_keys:
                    conn.execute(
                        "INSERT OR IGNORE INTO tracks (name, created_at) VALUES (?, ?)",
                        (key, time.time()),
                    )
                conn.execute("""
                    UPDATE laps SET track_id = (
                        SELECT id FROM tracks WHERE tracks.name = laps.track_key
                    ) WHERE track_id IS NULL
                """)

            for col in (
                "fuel_level", "tire_temp_fl", "tire_temp_fr",
                "tire_temp_rl", "tire_temp_rr",
            ):
                self._try_alter("lap_frames", f"{col} REAL")

            # `track_key` era NOT NULL. Como o código novo não a preenche mais,
            # toda inserção falharia enquanto a restrição existisse — e o SQLite
            # não relaxa restrição via ALTER, só reconstruindo a tabela.
            info = conn.execute("PRAGMA table_info(laps)").fetchall()
            if any(r[1] == "track_key" and r[3] == 1 for r in info):
                conn.execute("""
                    CREATE TABLE laps_rebuilt (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        track_id INTEGER,
                        lap_time_ms INTEGER NOT NULL,
                        recorded_at REAL NOT NULL,
                        frame_count INTEGER NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO laps_rebuilt (id, track_id, lap_time_ms, recorded_at, frame_count)
                    SELECT id, track_id, lap_time_ms, recorded_at, frame_count FROM laps
                """)
                conn.execute("DROP TABLE laps")
                conn.execute("ALTER TABLE laps_rebuilt RENAME TO laps")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_times (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
                    sector_index INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL
                )
            """)

            # Recalcula setores das voltas anteriores a esta tabela, a partir
            # das amostras já gravadas — senão a informação se perderia.
            old_laps = conn.execute(
                "SELECT id, track_id FROM laps "
                "WHERE id NOT IN (SELECT DISTINCT lap_id FROM sector_times)"
            ).fetchall()
            for lap_id, lap_track_id in old_laps:
                for i, sector_ms in enumerate(
                    compute_sector_times(conn, lap_id, 3, track_id=lap_track_id)
                ):
                    conn.execute(
                        "INSERT INTO sector_times (lap_id, sector_index, time_ms) "
                        "VALUES (?, ?, ?)",
                        (lap_id, i, sector_ms),
                    )

        if current_version < 4:
            # 3 -> 4: identificação de carro, posição de mundo (x/z, já decodificada
            # mas até então descartada) e a flag is_player.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                )
            """)
            self._try_alter("laps", "car_id INTEGER REFERENCES cars(id)")
            self._try_alter("laps", "is_player INTEGER NOT NULL DEFAULT 1")
            self._try_alter("lap_frames", "position_x REAL")
            self._try_alter("lap_frames", "position_z REAL")

        if current_version < 5:
            for col in (
                "g_lateral", "g_longitudinal",
                "suspension_fl", "suspension_fr", "suspension_rl", "suspension_rr",
                "tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr",
                "turbo_boost", "oil_temp", "water_temp",
            ):
                self._try_alter("lap_frames", f"{col} REAL")

        if current_version < 6:
            self._migrate_to_v6()

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _migrate_to_v6(self) -> None:
        """5 → 6: marca de volta completa, setores por pista e a chave
        estrangeira que faltava em `laps.track_id`.

        Três mudanças que precisam vir juntas porque duas delas exigem
        reconstruir a tabela `laps` — o SQLite não adiciona restrição por ALTER.

        1. `is_complete`: distingue volta gravada do início da que começou a ser
           observada no meio (app conectado com a volta em andamento). Sem isso,
           meia volta com o tempo cheio do jogo virava recorde.
        2. `tracks.sector_fractions`: permite ajustar onde caem os limites de
           setor por pista, em vez de assumir terços de distância.
        3. `laps.track_id REFERENCES tracks(id)`: `car_id` sempre teve a
           restrição, `track_id` não — dava para gravar volta apontando para
           pista inexistente.

        Voltas órfãs (apontando para pista que não existe) têm o `track_id`
        zerado em vez de serem apagadas: elas já eram invisíveis nas listagens,
        e destruir dado do usuário numa migração não se justifica.
        """
        conn = self._conn
        self._try_alter("laps", "is_complete INTEGER NOT NULL DEFAULT 1")
        self._try_alter("tracks", "sector_fractions TEXT")

        columns = [r[1] for r in conn.execute("PRAGMA table_info(laps)").fetchall()]
        has_fk = any(
            r[2] == "tracks"
            for r in conn.execute("PRAGMA foreign_key_list(laps)").fetchall()
        )
        if has_fk:
            return  # banco criado do zero já nasce com a restrição

        orphans = conn.execute(
            "SELECT COUNT(*) FROM laps WHERE track_id IS NOT NULL "
            "AND track_id NOT IN (SELECT id FROM tracks)"
        ).fetchone()[0]
        if orphans:
            print(f"[schema v6] {orphans} volta(s) sem pista válida: track_id zerado.")

        # A reconstrução precisa das FKs desligadas: `lap_frames` e `sector_times`
        # referenciam `laps(id)` em cascata, e o DROP dispararia a exclusão delas.
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("""
                CREATE TABLE laps_v6 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER REFERENCES tracks(id),
                    car_id INTEGER REFERENCES cars(id),
                    is_player INTEGER NOT NULL DEFAULT 1,
                    is_complete INTEGER NOT NULL DEFAULT 1,
                    lap_time_ms INTEGER NOT NULL,
                    recorded_at REAL NOT NULL,
                    frame_count INTEGER NOT NULL
                )
            """)
            car_col = "car_id" if "car_id" in columns else "NULL"
            player_col = "is_player" if "is_player" in columns else "1"
            conn.execute(f"""
                INSERT INTO laps_v6
                    (id, track_id, car_id, is_player, is_complete,
                     lap_time_ms, recorded_at, frame_count)
                SELECT id,
                       CASE WHEN track_id IN (SELECT id FROM tracks)
                            THEN track_id ELSE NULL END,
                       {car_col}, {player_col}, 1,
                       lap_time_ms, recorded_at, frame_count
                FROM laps
            """)
            conn.execute("DROP TABLE laps")
            conn.execute("ALTER TABLE laps_v6 RENAME TO laps")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _try_alter(self, table: str, column_def: str) -> None:
        """ADD COLUMN tolerante a coluna já existente."""
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
        except sqlite3.OperationalError:
            pass


def reference_lap_distance(conn, track_id, exclude_lap_id=None) -> float | None:
    """Distância 'canônica' da pista, para ancorar os limites de setor.

    O GT7 não expõe os pontos oficiais de setor. Usar a distância total de cada
    volta isoladamente faria o "setor 2" cair num ponto físico diferente a cada
    volta (uma volta mais longa desloca todos os limites). A mediana das últimas
    voltas dá uma referência estável; só a primeira volta de uma pista define os
    próprios limites, por não haver com o que comparar.
    """
    if track_id is None:
        return None
    query = "SELECT id FROM laps WHERE track_id = ? AND is_player = 1"
    params: list = [track_id]
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
    return totals[len(totals) // 2]


def sector_fractions_for(conn, track_id) -> list[float] | None:
    """Frações de distância onde os setores da pista terminam, ou None.

    Guardadas como texto separado por vírgula em `tracks.sector_fractions`
    (ex.: "0.31,0.68,1.0"). None significa "use a divisão padrão".

    Existe porque o GT7 não transmite os pontos oficiais de setor: dividir a
    volta em terços é um palpite, e quem conhece o circuito consegue alinhar os
    cortes aos setores reais do traçado.
    """
    if track_id is None:
        return None
    row = conn.execute(
        "SELECT sector_fractions FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        fractions = [float(x) for x in str(row[0]).split(",") if x.strip()]
    except ValueError:
        return None
    # Precisam ser crescentes, dentro de (0, 1] — configuração inválida cai
    # silenciosamente para o padrão em vez de produzir setores sem sentido.
    if not fractions or any(f <= 0 or f > 1 for f in fractions):
        return None
    if any(b <= a for a, b in zip(fractions, fractions[1:])):
        return None
    return fractions


def compute_sector_times(
    conn, lap_id: int, num_sectors: int, track_id=None
) -> list[int]:
    """Divide a volta em setores por **distância**, não por tempo.

    Usa as frações configuradas para a pista quando existem; senão, divide em
    trechos iguais. A divisão igual é aproximada — sem os pontos oficiais de
    setor, é o melhor palpite — mas suficiente para localizar em que trecho da
    volta houve ganho ou perda.
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

    reference_distance = reference_lap_distance(conn, track_id, exclude_lap_id=lap_id)
    # Referência absurdamente menor que esta volta significa histórico ruim
    # (voltas parciais/abortadas); melhor cair para a distância da própria volta.
    if reference_distance and reference_distance < lap_total_distance * 0.3:
        reference_distance = None
    total_distance = reference_distance or lap_total_distance

    fractions = sector_fractions_for(conn, track_id)
    if fractions:
        num_sectors = len(fractions)
    else:
        fractions = [i / num_sectors for i in range(1, num_sectors + 1)]
    boundaries = [total_distance * f for f in fractions]

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
