"""
Repositórios SQLite — o único lugar do sistema que conhece SQL.

Portado de `src/infrastructure/repositories/`. Duas propriedades do original que
a auditoria classificou como corretas foram preservadas:

- **`save` numa transação única.** A versão anterior à refatoração fazia dois
  commits, e uma falha entre eles deixava volta gravada sem setores — estado que
  o histórico exibia sem erro nenhum.
- **Colunas derivadas do modelo.** `_FRAME_COLUMNS` vem de `fields(TelemetryPoint)`,
  então um campo novo não sai de sincronia com o SELECT/INSERT em silêncio.

O que mudou: retenção configurável (P8) e `SessionRepository` (P9).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import fields
from datetime import datetime
from sqlite3 import Row

from ..domain.models import Car, Lap, Session, TelemetryPoint, Track
from ..observability.logging import get_logger
from .database import (
    UNKNOWN_CAR_NAME,
    SqliteDatabase,
    compute_sector_times,
)

_log = get_logger(__name__)

# Derivada do modelo, não escrita à mão. A tabela tem colunas extras (id,
# lap_id, seq) que não são do domínio e ficam de fora.
_FRAME_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(TelemetryPoint))
_FRAME_COLUMN_SQL = ", ".join(_FRAME_COLUMNS)
_FRAME_PLACEHOLDERS = ", ".join("?" * len(_FRAME_COLUMNS))

_LAP_COLUMNS = "id, session_id, track_id, car_id, is_player, lap_time_ms, recorded_at"


class SqliteLapRepository:
    """Voltas e suas amostras."""

    def __init__(
        self,
        database: SqliteDatabase,
        *,
        num_sectors: int = 3,
        keep_recent_per_track: int = 20,
        keep_best_per_track: int = 5,
    ) -> None:
        self._db = database
        self._num_sectors = num_sectors
        # Política de retenção vinda da configuração. 0 em ambos desliga a
        # exclusão automática — antes era constante fixa no módulo de banco e
        # apagava dado do usuário sem aviso nem controle.
        self._keep_recent = keep_recent_per_track
        self._keep_best = keep_best_per_track

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.connection

    # ---------- escrita ----------

    def save(self, lap: Lap) -> int:
        """Grava volta + amostras + setores numa **única transação**.

        Ou entra tudo, ou não entra nada. A retenção roda dentro da mesma
        transação: a volta recém-inserida já é visível nesta conexão, então
        conta corretamente para "melhores" e "recentes".
        """
        with self._db.lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute(
                    "INSERT INTO laps (session_id, track_id, car_id, is_player, "
                    "lap_time_ms, recorded_at, frame_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        lap.session_id,
                        lap.track_id,
                        lap.car_id,
                        1 if lap.is_player else 0,
                        lap.lap_time_ms,
                        lap.start_time.timestamp() if lap.start_time else time.time(),
                        len(lap.points),
                    ),
                )
                lap_id = int(cursor.lastrowid or 0)

                cursor.executemany(
                    f"INSERT INTO lap_frames (lap_id, seq, {_FRAME_COLUMN_SQL}) "
                    f"VALUES (?, ?, {_FRAME_PLACEHOLDERS})",
                    [
                        (lap_id, seq, *(getattr(p, c) for c in _FRAME_COLUMNS))
                        for seq, p in enumerate(lap.points)
                    ],
                )

                sectors = compute_sector_times(
                    self._conn, lap_id, self._num_sectors, track_id=lap.track_id
                )
                cursor.executemany(
                    "INSERT INTO sector_times (lap_id, sector_index, time_ms) "
                    "VALUES (?, ?, ?)",
                    [(lap_id, i, ms) for i, ms in enumerate(sectors)],
                )

                self._enforce_retention(lap.track_id)
                self._conn.commit()

                _log.info(
                    "volta gravada",
                    extra={"lap_id": lap_id, "samples": len(lap.points)},
                )
                return lap_id
            except Exception:
                self._conn.rollback()
                raise

    def _enforce_retention(self, track_id: int | None) -> None:
        """Mantém as N mais rápidas + as M mais recentes da pista.

        Só considera voltas de jogador nos dois critérios — voltas de replay/IA
        nunca contam como recorde nem como recente, e são as primeiras a sair.

        Com ambos os limites em 0 a retenção é desligada e nada é apagado.
        """
        if track_id is None or (self._keep_recent <= 0 and self._keep_best <= 0):
            return

        keep_ids: set[int] = set()
        if self._keep_best > 0:
            keep_ids.update(
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 "
                    "ORDER BY lap_time_ms ASC LIMIT ?",
                    (track_id, self._keep_best),
                ).fetchall()
            )
        if self._keep_recent > 0:
            keep_ids.update(
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 "
                    "ORDER BY recorded_at DESC LIMIT ?",
                    (track_id, self._keep_recent),
                ).fetchall()
            )

        if not keep_ids:
            # Nada a preservar significa que não há volta de jogador nesta
            # pista; apagar "tudo que não está na lista vazia" removeria voltas
            # legítimas de replay. Melhor não mexer.
            return

        placeholders = ",".join("?" * len(keep_ids))
        cursor = self._conn.execute(
            f"DELETE FROM laps WHERE track_id = ? AND id NOT IN ({placeholders})",
            (track_id, *keep_ids),
        )
        if cursor.rowcount > 0:
            _log.info(
                "retenção aplicou exclusão",
                extra={"track_id": track_id, "deleted": cursor.rowcount},
            )

    def delete(self, lap_id: int) -> None:
        with self._db.lock:
            self._conn.execute("DELETE FROM laps WHERE id = ?", (lap_id,))
            self._conn.commit()

    def delete_by_track(self, track_id: int) -> None:
        with self._db.lock:
            self._conn.execute("DELETE FROM laps WHERE track_id = ?", (track_id,))
            self._conn.commit()

    # ---------- leitura ----------

    def get_by_id(self, lap_id: int) -> Lap | None:
        row = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE id = ?", (lap_id,)
        ).fetchone()
        if row is None:
            return None
        lap = self._row_to_lap(row)
        lap.points = self.load_points(lap_id)
        return lap

    def get_all(self, limit: int | None = None) -> list[Lap]:
        sql = (
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE is_player = 1 "
            "ORDER BY recorded_at DESC"
        )
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        sql = (
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE track_id = ? AND is_player = 1 "
            "ORDER BY recorded_at DESC"
        )
        params: tuple[object, ...] = (track_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (track_id, limit)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_by_session(self, session_id: int) -> list[Lap]:
        rows = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE session_id = ? "
            "ORDER BY recorded_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row_to_lap(r) for r in rows]

    def get_best(self, track_id: int) -> Lap | None:
        row = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE track_id = ? AND is_player = 1 "
            "ORDER BY lap_time_ms ASC LIMIT 1",
            (track_id,),
        ).fetchone()
        return self._row_to_lap(row) if row else None

    def get_top(self, track_id: int, limit: int = 5) -> list[Lap]:
        rows = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM laps WHERE track_id = ? AND is_player = 1 "
            "ORDER BY lap_time_ms ASC LIMIT ?",
            (track_id, limit),
        ).fetchall()
        return [self._row_to_lap(r) for r in rows]

    def load_points(self, lap_id: int) -> list[TelemetryPoint]:
        """Amostras da volta, em ordem.

        Colunas criadas em migrações posteriores vêm NULL em voltas antigas — o
        `TelemetryPoint` carrega esses None e o `LapSeries` os trata como lacuna
        de amostragem em vez de quebrar o gráfico.
        """
        rows = self._conn.execute(
            f"SELECT {_FRAME_COLUMN_SQL} FROM lap_frames WHERE lap_id = ? "
            "ORDER BY seq ASC",
            (lap_id,),
        ).fetchall()
        return [TelemetryPoint(*row) for row in rows]

    def get_sector_times(self, lap_id: int) -> list[int | None]:
        rows = self._conn.execute(
            "SELECT time_ms FROM sector_times WHERE lap_id = ? "
            "ORDER BY sector_index ASC",
            (lap_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_sector_times_batch(self, lap_ids: list[int]) -> dict[int, list[int | None]]:
        """Setores de várias voltas numa consulta só.

        Existe para matar o padrão N+1 da tela de histórico, que consultava setor
        a setor dentro do laço de renderização — 50 voltas viravam 51 consultas.
        """
        if not lap_ids:
            return {}
        placeholders = ",".join("?" * len(lap_ids))
        rows = self._conn.execute(
            f"SELECT lap_id, sector_index, time_ms FROM sector_times "
            f"WHERE lap_id IN ({placeholders}) ORDER BY lap_id, sector_index ASC",
            lap_ids,
        ).fetchall()
        result: dict[int, list[int | None]] = {lid: [] for lid in lap_ids}
        for lap_id, _index, time_ms in rows:
            result[lap_id].append(time_ms)
        return result

    @staticmethod
    def _row_to_lap(row: Row) -> Lap:
        """Linha da tabela `laps` → modelo de domínio, sem as amostras."""
        lap_id, session_id, track_id, car_id, is_player, lap_time_ms, recorded_at = row
        return Lap(
            id=lap_id,
            session_id=session_id,
            track_id=track_id,
            car_id=car_id,
            is_player=bool(is_player),
            lap_time_ms=lap_time_ms,
            start_time=datetime.fromtimestamp(recorded_at) if recorded_at else None,
            points=[],
        )


class SqliteSessionRepository:
    """Sessões — a tabela que não existia (P9).

    Sem ela, `Session` vivia só em memória e morria com o processo, e
    "recuperar sessão após falha" (§8) era impossível.
    """

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.connection

    def start(self, track_id: int | None, car_id: int | None) -> int:
        with self._db.lock:
            cursor = self._conn.execute(
                "INSERT INTO sessions (track_id, car_id, started_at) VALUES (?, ?, ?)",
                (track_id, car_id, time.time()),
            )
            self._conn.commit()
            session_id = int(cursor.lastrowid or 0)
        _log.info("sessão iniciada", extra={"session_id": session_id})
        return session_id

    def finish(self, session_id: int, lap_count: int) -> None:
        with self._db.lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, lap_count = ? WHERE id = ?",
                (time.time(), lap_count, session_id),
            )
            self._conn.commit()
        _log.info(
            "sessão encerrada", extra={"session_id": session_id, "laps": lap_count}
        )

    def get_by_id(self, session_id: int) -> Session | None:
        row = self._conn.execute(
            "SELECT id, track_id, car_id, started_at, ended_at FROM sessions "
            "WHERE id = ?",
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def get_recent(self, limit: int = 50) -> list[Session]:
        rows = self._conn.execute(
            "SELECT id, track_id, car_id, started_at, ended_at FROM sessions "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def find_unfinished(self) -> list[Session]:
        """Sessões sem `ended_at` — o app caiu ou foi morto no meio.

        É o que torna a recuperação após falha do §8 possível: ao iniciar, a
        aplicação pode oferecer retomar ou encerrar o que ficou aberto.
        """
        rows = self._conn.execute(
            "SELECT id, track_id, car_id, started_at, ended_at FROM sessions "
            "WHERE ended_at IS NULL ORDER BY started_at DESC"
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def delete(self, session_id: int) -> None:
        with self._db.lock:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    @staticmethod
    def _row_to_session(row: Row) -> Session:
        session_id, track_id, car_id, started_at, ended_at = row
        return Session(
            id=session_id,
            track=Track(id=track_id) if track_id else None,
            car=Car(id=car_id) if car_id else None,
            start=datetime.fromtimestamp(started_at) if started_at else None,
            end=datetime.fromtimestamp(ended_at) if ended_at else None,
        )


class SqliteTrackRepository:
    """Pistas conhecidas pelo usuário."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.connection

    def get_or_create(self, name: str) -> int:
        clean = name.strip()
        if not clean:
            raise ValueError("nome de pista vazio")
        # A leitura fica **dentro** do lock, junto da escrita. Deixá-la fora
        # parecia inofensivo — é só um SELECT — mas a conexão é uma só,
        # compartilhada com `check_same_thread=False`, e ler por ela enquanto
        # outra thread escreve é uso concorrente do mesmo objeto sqlite3. O
        # sintoma não é exceção: é segmentation fault, e só aparece quando
        # alguém passa a chamar isto fora da thread da interface.
        with self._db.lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO tracks (name, created_at) VALUES (?, ?)",
                (clean, time.time()),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM tracks WHERE name = ?", (clean,)
            ).fetchone()
        return int(row[0])

    def get_by_id(self, track_id: int) -> Track | None:
        row = self._conn.execute(
            "SELECT id, name FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return Track(id=row[0], name=row[1]) if row else None

    def get_all(self) -> list[Track]:
        rows = self._conn.execute(
            "SELECT id, name FROM tracks ORDER BY name ASC"
        ).fetchall()
        return [Track(id=r[0], name=r[1]) for r in rows]

    def delete(self, track_id: int) -> None:
        with self._db.lock:
            self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._conn.commit()


class SqliteCarRepository:
    """Carros conhecidos pelo usuário."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.connection

    def get_or_create(self, name: str) -> int:
        clean = name.strip() or UNKNOWN_CAR_NAME
        with self._db.lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO cars (name, created_at) VALUES (?, ?)",
                (clean, time.time()),
            )
            self._conn.commit()
            # Dentro do lock, pelo mesmo motivo de `SqliteTrackRepository`.
            row = self._conn.execute(
                "SELECT id FROM cars WHERE name = ?", (clean,)
            ).fetchone()
        return int(row[0])

    def get_by_id(self, car_id: int) -> Car | None:
        row = self._conn.execute(
            "SELECT id, name FROM cars WHERE id = ?", (car_id,)
        ).fetchone()
        return Car(id=row[0], name=row[1]) if row else None

    def get_all(self) -> list[Car]:
        rows = self._conn.execute("SELECT id, name FROM cars ORDER BY name ASC").fetchall()
        return [Car(id=r[0], name=r[1]) for r in rows]
