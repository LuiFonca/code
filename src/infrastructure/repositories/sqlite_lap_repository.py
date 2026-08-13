"""
Implementação SQLite de `LapRepository`.

É aqui — e só aqui — que linha de banco vira `TelemetryPoint` e vice-versa.
Confinar essa tradução num único ponto é o que permitiu eliminar o antigo
`CHANNELS` (dicionário nome-do-canal → índice de tupla), que fazia a ordem das
colunas do SQLite vazar até a camada de gráficos.
"""

import time
from dataclasses import fields

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint
from .sqlite_database import (
    KEEP_BEST_PER_TRACK,
    KEEP_RECENT_PER_TRACK,
    SqliteDatabase,
    compute_sector_times,
)

# A lista de colunas é derivada do próprio modelo, não escrita à mão: assim um
# campo novo em TelemetryPoint não pode silenciosamente sair de sincronia com o
# SELECT/INSERT. A tabela tem colunas extras (id, lap_id, seq) que não são do
# domínio e ficam de fora.
_FRAME_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(TelemetryPoint))
_FRAME_COLUMN_SQL = ", ".join(_FRAME_COLUMNS)
_FRAME_PLACEHOLDERS = ", ".join("?" * len(_FRAME_COLUMNS))


class SqliteLapRepository(LapRepository):
    def __init__(self, database: SqliteDatabase, num_sectors: int = 3):
        self._db = database
        self._num_sectors = num_sectors

    @property
    def _conn(self):
        return self._db.connection

    # ---------- escrita ----------

    def save(self, lap: Lap) -> int:
        """Grava volta + amostras + setores numa **única transação**.

        A versão antiga fazia dois commits (um para a volta, outro dentro da
        retenção). Uma falha entre os dois deixava volta gravada sem setores —
        estado que o histórico exibia como volta sem tempo de setor, sem
        nenhum erro visível. Aqui, ou entra tudo, ou não entra nada.
        """
        with self._db.lock:
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "INSERT INTO laps (track_id, car_id, is_player, lap_time_ms, "
                    "recorded_at, frame_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        lap.track_id,
                        lap.car_id,
                        1 if lap.is_player else 0,
                        lap.lap_time_ms,
                        lap.start_time.timestamp() if lap.start_time else time.time(),
                        len(lap.points),
                    ),
                )
                lap_id = cur.lastrowid

                cur.executemany(
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
                cur.executemany(
                    "INSERT INTO sector_times (lap_id, sector_index, time_ms) "
                    "VALUES (?, ?, ?)",
                    [(lap_id, i, ms) for i, ms in enumerate(sectors)],
                )

                # Dentro da mesma transação: a volta recém-inserida já é visível
                # nesta conexão, então conta corretamente para "melhores"/"recentes".
                self._enforce_retention(lap.track_id)

                self._conn.commit()
                return lap_id
            except Exception:
                self._conn.rollback()
                raise

    def _enforce_retention(self, track_id: int | None) -> None:
        """Mantém as N mais rápidas + as N mais recentes da pista; apaga o resto
        (amostras e setores saem em cascata).

        Só considera voltas de jogador nos dois critérios — voltas de replay/IA
        nunca contam como recorde nem como recente, e são as primeiras a sair.
        """
        if track_id is None:
            return

        best_ids = [
            r[0] for r in self._conn.execute(
                "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 "
                "ORDER BY lap_time_ms ASC LIMIT ?",
                (track_id, KEEP_BEST_PER_TRACK),
            ).fetchall()
        ]
        recent_ids = [
            r[0] for r in self._conn.execute(
                "SELECT id FROM laps WHERE track_id = ? AND is_player = 1 "
                "ORDER BY recorded_at DESC LIMIT ?",
                (track_id, KEEP_RECENT_PER_TRACK),
            ).fetchall()
        ]

        keep_ids = set(best_ids) | set(recent_ids)
        if not keep_ids:
            # Nada a preservar significa que não há volta de jogador nesta
            # pista; apagar "tudo que não está na lista vazia" removeria voltas
            # legítimas de replay. Melhor não mexer.
            return

        placeholders = ",".join("?" * len(keep_ids))
        self._conn.execute(
            f"DELETE FROM laps WHERE track_id = ? AND id NOT IN ({placeholders})",
            (track_id, *keep_ids),
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
            "SELECT id, track_id, car_id, is_player, lap_time_ms, recorded_at "
            "FROM laps WHERE id = ?",
            (lap_id,),
        ).fetchone()
        if row is None:
            return None
        lap = self._row_to_lap(row)
        lap.points = self.load_points(lap_id)
        return lap

    def get_all(self, limit: int | None = None) -> list[Lap]:
        sql = (
            "SELECT id, track_id, car_id, is_player, lap_time_ms, recorded_at "
            "FROM laps WHERE is_player = 1 ORDER BY recorded_at DESC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        sql = (
            "SELECT id, track_id, car_id, is_player, lap_time_ms, recorded_at "
            "FROM laps WHERE track_id = ? AND is_player = 1 ORDER BY recorded_at DESC"
        )
        params: tuple = (track_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (track_id, limit)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_best(self, track_id: int) -> Lap | None:
        row = self._conn.execute(
            "SELECT id, track_id, car_id, is_player, lap_time_ms, recorded_at "
            "FROM laps WHERE track_id = ? AND is_player = 1 "
            "ORDER BY lap_time_ms ASC LIMIT 1",
            (track_id,),
        ).fetchone()
        return self._row_to_lap(row) if row else None

    def get_top(self, track_id: int, limit: int = KEEP_BEST_PER_TRACK) -> list[Lap]:
        rows = self._conn.execute(
            "SELECT id, track_id, car_id, is_player, lap_time_ms, recorded_at "
            "FROM laps WHERE track_id = ? AND is_player = 1 "
            "ORDER BY lap_time_ms ASC LIMIT ?",
            (track_id, limit),
        ).fetchall()
        return [self._row_to_lap(r) for r in rows]

    def load_points(self, lap_id: int) -> list[TelemetryPoint]:
        """Amostras da volta, em ordem.

        Colunas criadas em migrações posteriores vêm NULL em voltas antigas —
        o `TelemetryPoint` carrega esses None e o `LapSeries` os trata como
        lacuna de amostragem em vez de quebrar o gráfico.
        """
        rows = self._conn.execute(
            f"SELECT {_FRAME_COLUMN_SQL} FROM lap_frames WHERE lap_id = ? ORDER BY seq ASC",
            (lap_id,),
        ).fetchall()
        return [TelemetryPoint(*row) for row in rows]

    def get_sector_times(self, lap_id: int) -> list[int | None]:
        rows = self._conn.execute(
            "SELECT time_ms FROM sector_times WHERE lap_id = ? ORDER BY sector_index ASC",
            (lap_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_sector_times_batch(self, lap_ids: list[int]) -> dict[int, list[int | None]]:
        """Setores de várias voltas numa consulta só.

        Existe para matar o padrão N+1 da tela de histórico, que consultava
        setor a setor dentro do laço de renderização — 50 voltas viravam
        51 consultas.
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
        for lap_id, _idx, time_ms in rows:
            result[lap_id].append(time_ms)
        return result

    def car_name_for(self, lap_id: int) -> str | None:
        """Nome do carro da volta — atalho para o histórico, que exibe a coluna
        sem precisar montar o objeto `Car` inteiro."""
        row = self._conn.execute(
            "SELECT cars.name FROM laps LEFT JOIN cars ON cars.id = laps.car_id "
            "WHERE laps.id = ?",
            (lap_id,),
        ).fetchone()
        return row[0] if row else None

    def car_names_batch(self, lap_ids: list[int]) -> dict[int, str | None]:
        """Nomes de carro de várias voltas numa consulta — mesmo motivo do
        `get_sector_times_batch`."""
        if not lap_ids:
            return {}
        placeholders = ",".join("?" * len(lap_ids))
        rows = self._conn.execute(
            f"SELECT laps.id, cars.name FROM laps LEFT JOIN cars ON cars.id = laps.car_id "
            f"WHERE laps.id IN ({placeholders})",
            lap_ids,
        ).fetchall()
        return {lap_id: name for lap_id, name in rows}

    # ---------- mapeamento ----------

    @staticmethod
    def _row_to_lap(row) -> Lap:
        """Linha da tabela `laps` → modelo de domínio, sem as amostras."""
        from datetime import datetime

        lap_id, track_id, car_id, is_player, lap_time_ms, recorded_at = row
        return Lap(
            id=lap_id,
            track_id=track_id,
            car_id=car_id,
            is_player=bool(is_player),
            lap_time_ms=lap_time_ms,
            start_time=datetime.fromtimestamp(recorded_at) if recorded_at else None,
            points=[],
        )
