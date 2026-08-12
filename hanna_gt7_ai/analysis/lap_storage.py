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
SCHEMA_VERSION = 4

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
        old_laps = conn.execute(
            "SELECT id, track_id FROM laps WHERE id NOT IN (SELECT DISTINCT lap_id FROM sector_times)"
        ).fetchall()
        for lap_id, lap_track_id in old_laps:
            sectors = _compute_sector_times(conn, lap_id, 3, track_id=lap_track_id)
            for i, sector_ms in enumerate(sectors):
                conn.execute(
                    "INSERT INTO sector_times (lap_id, sector_index, time_ms) VALUES (?, ?, ?)",
                    (lap_id, i, sector_ms),
                )

    if current_version < 4:
        # Migração 3 -> 4: adiciona identificação de carro (tabela própria,
        # igual pistas — o GT7 não garante um ID de carro confiável nesta
        # implementação, então o nome é informado manualmente pelo usuário,
        # com "Desconhecido" como padrão), posição de mundo (x/z, já
        # decodificada do pacote mas até então descartada — usada para o
        # mapa/trajetória da volta) e a flag is_player (registrada por
        # transparência/auditoria; a gravação de voltas de replay/IA já é
        # bloqueada antes de chegar aqui, mas manter a flag no dado permite
        # filtrar no futuro sem reprocessar nada).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE laps ADD COLUMN car_id INTEGER REFERENCES cars(id)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE laps ADD COLUMN is_player INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN position_x REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE lap_frames ADD COLUMN position_z REAL")
        except sqlite3.OperationalError:
            pass

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db():
    """Cria as tabelas caso ainda não existam e roda migrações pendentes.
    Seguro de chamar toda vez que o app inicia."""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL
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
            track_id INTEGER,
            car_id INTEGER REFERENCES cars(id),
            is_player INTEGER NOT NULL DEFAULT 1,
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
            position_x REAL,
            position_z REAL
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
        LEFT JOIN laps ON laps.track_id = tracks.id AND laps.is_player = 1
        GROUP BY tracks.id
        ORDER BY tracks.name ASC
    """).fetchall()
    conn.close()
    return rows


# ---------- carros ----------

UNKNOWN_CAR_NAME = "Desconhecido"


def get_or_create_car(name: str) -> int:
    """Retorna o id do carro com esse nome, criando-o se não existir.
    O GT7 não expõe o modelo do carro nesta implementação (nenhum offset
    de car_id foi validado com dados reais), então o nome é sempre
    informado manualmente pelo usuário — "Desconhecido" quando ele não
    quer/consegue identificar o carro."""
    name = name.strip() or UNKNOWN_CAR_NAME
    conn = _connect()
    row = conn.execute("SELECT id FROM cars WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return row[0]

    cur = conn.cursor()
    cur.execute("INSERT INTO cars (name, created_at) VALUES (?, ?)", (name, time.time()))
    car_id = cur.lastrowid
    conn.commit()
    conn.close()
    return car_id


def list_cars():
    """Lista todos os carros já usados."""
    conn = _connect()
    rows = conn.execute("SELECT id, name FROM cars ORDER BY name ASC").fetchall()
    conn.close()
    return rows


# ---------- voltas ----------

def save_lap(track_id: int, car_id, lap_time_ms: int, frames: list, is_player: bool = True, num_sectors: int = 3) -> int:
    """Salva uma volta completa (tempo + frames + setores), aplica a
    política de retenção (5 melhores + 50 mais recentes por pista) e
    retorna o id gerado.

    `is_player` é gravado por transparência/auditoria, mas a decisão de
    NÃO chamar esta função para voltas de replay/IA já é tomada antes
    (LapRecorder), então nenhuma linha aqui deveria vir com is_player=0
    em uso normal — a flag existe para permitir filtrar no futuro sem
    reprocessar o banco, caso uma fonte de dados mais confiável apareça.

    A conexão é SEMPRE fechada, mesmo em caso de erro (try/finally) — uma
    conexão que fica aberta por uma exceção não tratada é o tipo de bug que
    trava o banco inteiro para chamadas seguintes ("database is locked"),
    o que é especialmente grave aqui porque essa função pode ser chamada
    repetidamente em alta frequência."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO laps (track_id, car_id, is_player, lap_time_ms, recorded_at, frame_count) VALUES (?, ?, ?, ?, ?, ?)",
            (track_id, car_id, 1 if is_player else 0, lap_time_ms, time.time(), len(frames)),
        )
        lap_id = cur.lastrowid
        cur.executemany(
            """INSERT INTO lap_frames
               (lap_id, seq, elapsed_ms, distance_m, speed_kmh, rpm, gear, throttle, brake,
                fuel_level, tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr,
                position_x, position_z)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    lap_id, i, f.elapsed_ms, f.distance_m, f.speed_kmh, f.rpm, f.gear,
                    f.throttle, f.brake, f.fuel_level,
                    f.tire_temp_fl, f.tire_temp_fr, f.tire_temp_rl, f.tire_temp_rr,
                    f.position_x, f.position_z,
                )
                for i, f in enumerate(frames)
            ],
        )
        conn.commit()

        sectors = _compute_sector_times(conn, lap_id, num_sectors, track_id=track_id)
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
    (frames e setores são removidos em cascata automaticamente).

    Só considera voltas de jogador (is_player=1) para os dois critérios —
    voltas não-jogador não deveriam existir aqui (LapRecorder já bloqueia
    isso antes), mas se existirem por qualquer motivo elas nunca contam
    como "melhor" nem como "recente" e são as primeiras a ser descartadas."""
    best_ids = [row[0] for row in conn.execute(
        "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 ORDER BY lap_time_ms ASC LIMIT ?",
        (track_id, KEEP_BEST_PER_TRACK),
    ).fetchall()]
    recent_ids = [row[0] for row in conn.execute(
        "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 ORDER BY recorded_at DESC LIMIT ?",
        (track_id, KEEP_RECENT_PER_TRACK),
    ).fetchall()]

    keep_ids = set(best_ids) | set(recent_ids)

    placeholders = ",".join("?" * len(keep_ids)) if keep_ids else "NULL"
    conn.execute(
        f"DELETE FROM laps WHERE track_id = ? AND id NOT IN ({placeholders})",
        (track_id, *keep_ids),
    )
    conn.commit()


def get_best_lap_time(track_id: int):
    """Retorna (id, lap_time_ms) da melhor volta salva na pista, ou None.
    Ignora voltas que não sejam de jogador (replay/IA)."""
    conn = _connect()
    row = conn.execute(
        "SELECT id, lap_time_ms FROM laps WHERE track_id = ? AND is_player = 1 ORDER BY lap_time_ms ASC LIMIT 1",
        (track_id,),
    ).fetchone()
    conn.close()
    return row


def get_top_laps(track_id: int, limit: int = KEEP_BEST_PER_TRACK):
    """As voltas mais rápidas salvas na pista (histórico de recordes),
    com o nome do carro quando disponível. Ignora voltas não-jogador."""
    conn = _connect()
    rows = conn.execute("""
        SELECT laps.id, laps.lap_time_ms, laps.recorded_at, cars.name
        FROM laps LEFT JOIN cars ON cars.id = laps.car_id
        WHERE laps.track_id = ? AND laps.is_player = 1
        ORDER BY laps.lap_time_ms ASC LIMIT ?
    """, (track_id, limit)).fetchall()
    conn.close()
    return rows


def get_lap_frames(lap_id: int):
    """Retorna todos os frames de uma volta, ordenados (usado na comparação e nos gráficos).
    Colunas antigas de voltas migradas de versões anteriores do schema podem
    vir como NULL (fuel_level/tire_temp_*/position_x/position_z não existiam
    antes) — quem consome este retorno precisa tratar None."""
    conn = _connect()
    rows = conn.execute(
        """SELECT elapsed_ms, distance_m, speed_kmh, rpm, gear, throttle, brake,
                  fuel_level, tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr,
                  position_x, position_z
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


def _reference_lap_distance(conn: sqlite3.Connection, track_id, exclude_lap_id=None):
    """Distância total 'canônica' da pista, usada para posicionar os
    limites de setor no mesmo ponto físico em todas as voltas.

    Sem pontos oficiais de setor (o GT7 não os expõe via telemetria), usar
    a distância total de CADA volta individualmente faria o 'Setor 2' cair
    num lugar diferente da pista em cada volta (uma volta mais longa desloca
    os limites). Em vez disso, usamos a mediana da distância total das
    últimas voltas de jogador já salvas nesta pista como referência estável;
    a volta atual só define seus próprios limites quando ainda não há
    nenhuma outra volta salva (primeira volta da pista)."""
    if track_id is None:
        return None
    query = "SELECT id FROM laps WHERE track_id = ? AND is_player = 1"
    params = [track_id]
    if exclude_lap_id is not None:
        query += " AND id != ?"
        params.append(exclude_lap_id)
    query += " ORDER BY recorded_at DESC LIMIT 10"
    lap_ids = [row[0] for row in conn.execute(query, params).fetchall()]
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


def _compute_sector_times(conn: sqlite3.Connection, lap_id: int, num_sectors: int, track_id=None):
    """Divide a volta em setores por distância percorrida (não por tempo).

    Limitação conhecida: como o protocolo do GT7 não expõe pontos oficiais
    de setor da pista, dividimos a volta em partes de distância igual
    (ex: 3 setores = início/meio/fim), usando como referência a distância
    total típica da pista (ver _reference_lap_distance) para que os limites
    caiam aproximadamente no mesmo ponto físico em voltas diferentes — sem
    isso, comparar 'Setor 2' entre duas voltas de distância total distinta
    não faria sentido. Isso é aproximado, mas suficiente para identificar
    em que trecho da volta houve ganho ou perda de tempo."""
    rows = conn.execute(
        "SELECT elapsed_ms, distance_m FROM lap_frames WHERE lap_id = ? ORDER BY seq ASC",
        (lap_id,),
    ).fetchall()

    if not rows:
        return []

    lap_total_distance = rows[-1][1]
    if lap_total_distance <= 0:
        return []

    reference_distance = _reference_lap_distance(conn, track_id, exclude_lap_id=lap_id)
    if reference_distance and reference_distance < lap_total_distance * 0.3:
        reference_distance = None
    total_distance = reference_distance if reference_distance else lap_total_distance

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

    if len(sector_times) < num_sectors:
        sector_times.append(rows[-1][0] - last_boundary_ms)

    return sector_times


def list_laps(track_id: int, limit: int = KEEP_RECENT_PER_TRACK):
    """Lista as voltas mais recentes salvas para essa pista, com nome do
    carro quando disponível. Ignora voltas não-jogador (replay/IA)."""
    conn = _connect()
    rows = conn.execute("""
        SELECT laps.id, laps.lap_time_ms, laps.recorded_at, cars.name
        FROM laps LEFT JOIN cars ON cars.id = laps.car_id
        WHERE laps.track_id = ? AND laps.is_player = 1
        ORDER BY laps.recorded_at DESC LIMIT ?
    """, (track_id, limit)).fetchall()
    conn.close()
    return rows
