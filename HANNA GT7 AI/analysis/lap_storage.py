"""
Persistência de voltas em SQLite.

Schema normalizado por pista: cada pista tem sua própria lista de voltas,
o app mantém automaticamente as 5 melhores + as 50 mais recentes de cada
pista (voltas mais antigas fora desse critério são descartadas para não
deixar o banco crescer indefinidamente).

Como o protocolo do GT7 não expõe o nome/id da pista atual, a identificação
de pista é MANUAL: o usuário informa o nome antes de conectar (ver
gui/main_window.py). Isso é uma limitação conhecida do protocolo, não do app.

Lógica pura, sem dependência de Qt/interface — pode ser testada e reutilizada
isoladamente.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".hanna_gt7_ai" / "laps.db"

# Versão do schema. Sempre que uma coluna/tabela nova for necessária,
# incrementar este número e adicionar a migração correspondente em
# _run_migrations(). Isso evita que o app quebre silenciosamente quando
# o banco de um usuário foi criado por uma versão anterior do código.
SCHEMA_VERSION = 3

# Quantas voltas manter por pista: as N mais rápidas (histórico de recordes)
# + as N mais recentes (histórico cronológico). Voltas fora dos dois
# critérios são apagadas automaticamente ao salvar uma volta nova.
KEEP_BEST_PER_TRACK = 5
KEEP_RECENT_PER_TRACK = 50


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _run_migrations(conn: sqlite3.Connection):
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]

    if current_version < 2:
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN elapsed_ms INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    if current_version < 3:
        # Migração 2 -> 3: introduz a tabela de pistas e campos novos por
        # frame (temperatura de pneu, combustível). Voltas salvas antes
        # desta versão são migradas automaticamente: o antigo "track_key"
        # (texto livre) vira o nome da pista na nova tabela `tracks`, sem
        # perda de dados.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            )
        """)

        try:
            conn.execute("ALTER TABLE laps ADD COLUMN track_id INTEGER REFERENCES tracks(id)")
        except sqlite3.OperationalError:
            pass

        # Só tenta migrar o track_key antigo se a coluna ainda existir
        # (bancos criados do zero, já no schema novo, nunca tiveram essa
        # coluna — nesse caso não há nada a migrar).
        existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(laps)").fetchall()]
        if "track_key" in existing_columns:
            old_keys = [row[0] for row in conn.execute(
                "SELECT DISTINCT track_key FROM laps WHERE track_key IS NOT NULL"
            ).fetchall()]
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

        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN fuel_level REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN tire_temp_fl REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN tire_temp_fr REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN tire_temp_rl REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN tire_temp_rr REAL")
        except sqlite3.OperationalError:
            pass

        # A coluna antiga track_key tinha restrição NOT NULL. Como o
        # código novo não preenche mais essa coluna (usa track_id), toda
        # inserção passaria a falhar se a restrição não for removida.
        # SQLite não permite "ALTER COLUMN" para relaxar uma restrição
        # diretamente — é preciso reconstruir a tabela.
        track_key_info = conn.execute("PRAGMA table_info(laps)").fetchall()
        track_key_is_not_null = any(
            row[1] == "track_key" and row[3] == 1 for row in track_key_info
        )
        if track_key_is_not_null:
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

        # Pré-calcula os setores das voltas antigas (que ainda não tinham
        # essa tabela) a partir dos frames já salvos, para não perder essa
        # informação na migração.
        old_lap_ids = [row[0] for row in conn.execute(
            "SELECT id FROM laps WHERE id NOT IN (SELECT DISTINCT lap_id FROM sector_times)"
        ).fetchall()]
        for lap_id in old_lap_ids:
            sectors = _compute_sector_times(conn, lap_id, 3)
            for i, sector_ms in enumerate(sectors):
                conn.execute(
                    "INSERT INTO sector_times (lap_id, sector_index, time_ms) VALUES (?, ?, ?)",
                    (lap_id, i, sector_ms),
                )

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db():
    """Cria as tabelas caso ainda não existam e roda migrações pendentes.
    Seguro de chamar toda vez que o app inicia."""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS laps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
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
            brake REAL NOT NULL
        )
    """)
    conn.commit()

    _run_migrations(conn)
    conn.close()


# ---------- pistas ----------

def get_or_create_track(name: str) -> int:
    """Retorna o id da pista com esse nome, criando-a se não existir."""
    name = name.strip() or "Pista não identificada"
    conn = _connect()
    row = conn.execute("SELECT id FROM tracks WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return row[0]

    cur = conn.cursor()
    cur.execute("INSERT INTO tracks (name, created_at) VALUES (?, ?)", (name, time.time()))
    track_id = cur.lastrowid
    conn.commit()
    conn.close()
    return track_id


def list_tracks():
    """Lista todas as pistas já usadas, com a quantidade de voltas salvas em cada uma."""
    conn = _connect()
    rows = conn.execute("""
        SELECT tracks.id, tracks.name, COUNT(laps.id) as lap_count
        FROM tracks
        LEFT JOIN laps ON laps.track_id = tracks.id
        GROUP BY tracks.id
        ORDER BY tracks.name ASC
    """).fetchall()
    conn.close()
    return rows


# ---------- voltas ----------

def save_lap(track_id: int, lap_time_ms: int, frames: list, num_sectors: int = 3) -> int:
    """Salva uma volta completa (tempo + frames + setores), aplica a
    política de retenção (5 melhores + 50 mais recentes por pista) e
    retorna o id gerado.

    A conexão é SEMPRE fechada, mesmo em caso de erro (try/finally) — uma
    conexão que fica aberta por uma exceção não tratada é o tipo de bug que
    trava o banco inteiro para chamadas seguintes ("database is locked"),
    o que é especialmente grave aqui porque essa função pode ser chamada
    repetidamente em alta frequência."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO laps (track_id, lap_time_ms, recorded_at, frame_count) VALUES (?, ?, ?, ?)",
            (track_id, lap_time_ms, time.time(), len(frames)),
        )
        lap_id = cur.lastrowid
        cur.executemany(
            """INSERT INTO lap_frames
               (lap_id, seq, elapsed_ms, distance_m, speed_kmh, rpm, gear, throttle, brake,
                fuel_level, tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    lap_id, i, f.elapsed_ms, f.distance_m, f.speed_kmh, f.rpm, f.gear,
                    f.throttle, f.brake, f.fuel_level,
                    f.tire_temp_fl, f.tire_temp_fr, f.tire_temp_rl, f.tire_temp_rr,
                )
                for i, f in enumerate(frames)
            ],
        )
        conn.commit()

        sectors = _compute_sector_times(conn, lap_id, num_sectors)
        cur.executemany(
            "INSERT INTO sector_times (lap_id, sector_index, time_ms) VALUES (?, ?, ?)",
            [(lap_id, i, sector_ms) for i, sector_ms in enumerate(sectors)],
        )
        conn.commit()

        _enforce_retention(conn, track_id)
        return lap_id
    finally:
        conn.close()


def _enforce_retention(conn: sqlite3.Connection, track_id: int):
    """Mantém apenas as KEEP_BEST_PER_TRACK voltas mais rápidas e as
    KEEP_RECENT_PER_TRACK mais recentes de uma pista; apaga o resto
    (frames e setores são removidos em cascata automaticamente)."""
    best_ids = [row[0] for row in conn.execute(
        "SELECT id FROM laps WHERE track_id = ? ORDER BY lap_time_ms ASC LIMIT ?",
        (track_id, KEEP_BEST_PER_TRACK),
    ).fetchall()]
    recent_ids = [row[0] for row in conn.execute(
        "SELECT id FROM laps WHERE track_id = ? ORDER BY recorded_at DESC LIMIT ?",
        (track_id, KEEP_RECENT_PER_TRACK),
    ).fetchall()]

    keep_ids = set(best_ids) | set(recent_ids)
    if not keep_ids:
        return

    placeholders = ",".join("?" * len(keep_ids))
    conn.execute(
        f"DELETE FROM laps WHERE track_id = ? AND id NOT IN ({placeholders})",
        (track_id, *keep_ids),
    )
    conn.commit()


def get_best_lap_time(track_id: int):
    """Retorna (id, lap_time_ms) da melhor volta salva na pista, ou None."""
    conn = _connect()
    row = conn.execute(
        "SELECT id, lap_time_ms FROM laps WHERE track_id = ? ORDER BY lap_time_ms ASC LIMIT 1",
        (track_id,),
    ).fetchone()
    conn.close()
    return row


def get_top_laps(track_id: int, limit: int = KEEP_BEST_PER_TRACK):
    """As voltas mais rápidas salvas na pista (histórico de recordes)."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, lap_time_ms, recorded_at FROM laps WHERE track_id = ? ORDER BY lap_time_ms ASC LIMIT ?",
        (track_id, limit),
    ).fetchall()
    conn.close()
    return rows


def get_lap_frames(lap_id: int):
    """Retorna todos os frames de uma volta, ordenados (usado na comparação e nos gráficos)."""
    conn = _connect()
    rows = conn.execute(
        """SELECT elapsed_ms, distance_m, speed_kmh, rpm, gear, throttle, brake,
                  fuel_level, tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr
           FROM lap_frames WHERE lap_id = ? ORDER BY seq ASC""",
        (lap_id,),
    ).fetchall()
    conn.close()
    return rows


def get_sector_times(lap_id: int):
    """Retorna os tempos de setor já calculados e salvos para essa volta,
    em ordem (setor 1, setor 2, ...), em milissegundos."""
    conn = _connect()
    rows = conn.execute(
        "SELECT time_ms FROM sector_times WHERE lap_id = ? ORDER BY sector_index ASC",
        (lap_id,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def _compute_sector_times(conn: sqlite3.Connection, lap_id: int, num_sectors: int):
    """Divide a volta em setores por distância percorrida (não por tempo).

    Limitação conhecida: como o protocolo do GT7 não expõe pontos oficiais
    de setor da pista, dividimos a volta em partes de distância igual
    (ex: 3 setores = início/meio/fim). Isso é aproximado, mas suficiente
    para identificar em que trecho da volta houve ganho ou perda de tempo."""
    rows = conn.execute(
        "SELECT elapsed_ms, distance_m FROM lap_frames WHERE lap_id = ? ORDER BY seq ASC",
        (lap_id,),
    ).fetchall()

    if not rows:
        return []

    total_distance = rows[-1][1]
    if total_distance <= 0:
        return []

    boundaries_distance = [total_distance * (i / num_sectors) for i in range(1, num_sectors + 1)]

    sector_times = []
    last_boundary_ms = rows[0][0]
    boundary_index = 0

    for elapsed_ms, distance_m in rows:
        if boundary_index >= num_sectors:
            break
        if distance_m >= boundaries_distance[boundary_index]:
            sector_times.append(elapsed_ms - last_boundary_ms)
            last_boundary_ms = elapsed_ms
            boundary_index += 1

    while len(sector_times) < num_sectors:
        sector_times.append(rows[-1][0] - last_boundary_ms)
        last_boundary_ms = rows[-1][0]

    return sector_times


def list_laps(track_id: int, limit: int = KEEP_RECENT_PER_TRACK):
    """Lista as voltas mais recentes salvas para essa pista."""
    conn = _connect()
    rows = conn.execute(
        """SELECT id, lap_time_ms, recorded_at FROM laps
           WHERE track_id = ? ORDER BY recorded_at DESC LIMIT ?""",
        (track_id, limit),
    ).fetchall()
    conn.close()
    return rows
