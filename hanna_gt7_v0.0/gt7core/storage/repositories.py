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
from ..domain.validity import MIN_COMPLETE_RATIO
from ..observability.logging import get_logger
from .database import (
    UNKNOWN_CAR_NAME,
    SqliteDatabase,
    compute_sector_times,
    sector_anchor_for,
)

_log = get_logger(__name__)

# Derivada do modelo, não escrita à mão. A tabela tem colunas extras (id,
# lap_id, seq) que não são do domínio e ficam de fora.
_FRAME_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(TelemetryPoint))
_FRAME_COLUMN_SQL = ", ".join(_FRAME_COLUMNS)
_FRAME_PLACEHOLDERS = ", ".join("?" * len(_FRAME_COLUMNS))


def _same_anchor(a: float | None, b: float | None) -> bool:
    """Duas âncoras são a mesma régua?

    Tolerância de 1 cm: a âncora trafega como REAL pelo SQLite e volta com o
    ruído de ponto flutuante de sempre. Sem a folga, toda volta pareceria
    desatualizada e a tela recalcularia tudo a cada abertura — exatamente o
    custo que a releitura existe para eliminar.
    """
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 0.01


#: A distância da volta e o comprimento da pista viajam **juntos** em toda
#: leitura: validade é a razão entre os dois, e trazer só um produziria uma
#: volta que não sabe dizer se deu a volta.
_LAP_COLUMNS = (
    "l.id, l.session_id, l.track_id, l.car_id, l.is_player, l.lap_time_ms, "
    "l.recorded_at, l.distance_m, t.length_m"
)
_LAP_FROM = "laps l LEFT JOIN tracks t ON t.id = l.track_id"

#: Volta elegível a recorde. Quando falta a distância ou o comprimento da pista
#: a volta **passa**: não se pode acusar de incompleta uma volta que ninguém
#: teve como medir, e o limiar do domínio mora em `domain.validity`.
_COMPLETE_ENOUGH = (
    "(t.length_m IS NULL OR l.distance_m IS NULL "
    f"OR l.distance_m >= t.length_m * {MIN_COMPLETE_RATIO})"
)


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
                    "lap_time_ms, recorded_at, frame_count, distance_m) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        lap.session_id,
                        lap.track_id,
                        lap.car_id,
                        1 if lap.is_player else 0,
                        lap.lap_time_ms,
                        lap.start_time.timestamp() if lap.start_time else time.time(),
                        len(lap.points),
                        lap.measured_distance_m,
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

                # A âncora é resolvida **uma vez** e gravada junto: quem ler
                # os setores guardados precisa saber com que régua foram
                # medidos. Sem esse registro, a única leitura segura seria
                # recalcular tudo a cada abertura da tela.
                anchor = sector_anchor_for(
                    self._conn, lap.track_id, exclude_lap_id=lap_id
                )
                cursor.execute(
                    "UPDATE laps SET sector_anchor_m = ? WHERE id = ?",
                    (anchor, lap_id),
                )
                sectors = compute_sector_times(
                    self._conn,
                    lap_id,
                    self._num_sectors,
                    track_id=lap.track_id,
                    anchor_m=anchor,
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
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} WHERE l.id = ?", (lap_id,)
        ).fetchone()
        if row is None:
            return None
        lap = self._row_to_lap(row)
        lap.points = self.load_points(lap_id)
        return lap

    def get_all(self, limit: int | None = None) -> list[Lap]:
        sql = (
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} WHERE l.is_player = 1 "
            "ORDER BY l.recorded_at DESC"
        )
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        sql = (
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} WHERE l.track_id = ? AND l.is_player = 1 "
            "ORDER BY l.recorded_at DESC"
        )
        params: tuple[object, ...] = (track_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (track_id, limit)
        return [self._row_to_lap(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_by_session(self, session_id: int) -> list[Lap]:
        rows = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} WHERE l.session_id = ? "
            "ORDER BY l.recorded_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row_to_lap(r) for r in rows]

    def get_best(self, track_id: int) -> Lap | None:
        """A melhor volta **completa** da pista.

        Uma volta que cortou o traçado produz um tempo mais rápido do que o
        piloto fez, e recorde é a referência do delta na tela, do alvo da
        sessão e da comparação — um tempo falso aqui contamina tudo o que
        depende dele.

        Voltas longas continuam elegíveis: sair da pista acrescenta distância e
        tempo, então elas são mais lentas e nunca disputariam o recorde de
        qualquer forma. Excluí-las seria rigor sem efeito.
        """
        row = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} "
            "WHERE l.track_id = ? AND l.is_player = 1 "
            f"AND {_COMPLETE_ENOUGH} "
            "ORDER BY l.lap_time_ms ASC LIMIT 1",
            (track_id,),
        ).fetchone()
        return self._row_to_lap(row) if row else None

    def get_top(self, track_id: int, limit: int = 5) -> list[Lap]:
        rows = self._conn.execute(
            f"SELECT {_LAP_COLUMNS} FROM {_LAP_FROM} WHERE l.track_id = ? AND l.is_player = 1 "
            "ORDER BY l.lap_time_ms ASC LIMIT ?",
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

    def sector_anchor(self, track_id: int | None) -> float | None:
        """A régua com que os setores desta pista são medidos.

        As telas que desenham as divisas no mapa precisam da mesma âncora que a
        gravação usou, senão a marca "S1" no traçado cai num ponto e o tempo do
        setor 1 na tabela é medido noutro — que foi exatamente o estado
        anterior.
        """
        return sector_anchor_for(self._conn, track_id)

    def sector_times_for_track(
        self, track_id: int, lap_ids: list[int]
    ) -> dict[int, list[int | None]]:
        """Setores das voltas informadas, comparáveis entre si.

        Lê o que está gravado em vez de recalcular. A tela de histórico
        recalculava as três divisas de cada volta a cada abertura — 797 ms num
        acervo de 50 voltas — para chegar a números que já estavam no banco.

        A releitura só é válida porque a volta guarda **com que âncora** foi
        medida. Uma volta cuja âncora não bate com a atual é recalculada e
        regravada aqui mesmo: acontece quando a pista ganha comprimento de
        catálogo depois de já ter voltas gravadas, e o conserto é permanente em
        vez de repetido a cada abertura.
        """
        if not lap_ids:
            return {}

        atual = sector_anchor_for(self._conn, track_id)
        gravados = self.get_sector_times_batch(lap_ids)

        placeholders = ",".join("?" * len(lap_ids))
        ancoras = {
            r[0]: r[1]
            for r in self._conn.execute(
                f"SELECT id, sector_anchor_m FROM laps WHERE id IN ({placeholders})",
                lap_ids,
            ).fetchall()
        }

        desatualizadas = [
            lid
            for lid in lap_ids
            if not _same_anchor(ancoras.get(lid), atual)
            or len(gravados.get(lid, [])) != self._num_sectors
        ]

        for lid in desatualizadas:
            setores = compute_sector_times(
                self._conn, lid, self._num_sectors, track_id=track_id, anchor_m=atual
            )
            if not setores:
                continue
            with self._db.lock:
                self._conn.execute(
                    "DELETE FROM sector_times WHERE lap_id = ?", (lid,)
                )
                self._conn.executemany(
                    "INSERT INTO sector_times (lap_id, sector_index, time_ms) "
                    "VALUES (?, ?, ?)",
                    [(lid, i, ms) for i, ms in enumerate(setores)],
                )
                self._conn.execute(
                    "UPDATE laps SET sector_anchor_m = ? WHERE id = ?", (atual, lid)
                )
                self._conn.commit()
            gravados[lid] = list(setores)

        if desatualizadas:
            _log.info(
                "setores realinhados à âncora atual",
                extra={"track_id": track_id, "laps": len(desatualizadas)},
            )
        return gravados

    @staticmethod
    def _row_to_lap(row: Row) -> Lap:
        """Linha da tabela `laps` → modelo de domínio, sem as amostras."""
        (
            lap_id, session_id, track_id, car_id, is_player, lap_time_ms,
            recorded_at, distance_m, track_length_m,
        ) = row
        return Lap(
            id=lap_id,
            session_id=session_id,
            track_id=track_id,
            car_id=car_id,
            is_player=bool(is_player),
            lap_time_ms=lap_time_ms,
            start_time=datetime.fromtimestamp(recorded_at) if recorded_at else None,
            points=[],
            distance_m=distance_m,
            track_length_m=track_length_m,
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

    def get_or_create(self, name: str, length_m: float | None = None) -> int:
        """Id da pista, criando-a se preciso.

        `length_m` é o comprimento oficial do catálogo do jogo, e é o que ancora
        as divisas de setor num ponto físico fixo da pista. Chega aqui porque a
        identificação já o tem em mãos: a pista é reconhecida **pelo**
        comprimento, e até agora esse número era usado para achar o nome e
        descartado em seguida.

        Uma pista que já existe sem comprimento é completada; uma que já tem
        comprimento é deixada em paz, porque sobrescrever mudaria de lugar as
        divisas de todas as voltas já gravadas.
        """
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
            if length_m and length_m > 0:
                self._conn.execute(
                    "UPDATE tracks SET length_m = ? "
                    "WHERE name = ? AND (length_m IS NULL OR length_m <= 0)",
                    (float(length_m), clean),
                )
                self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM tracks WHERE name = ?", (clean,)
            ).fetchone()
        return int(row[0])

    def get_by_id(self, track_id: int) -> Track | None:
        row = self._conn.execute(
            "SELECT id, name, length_m FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return Track(id=row[0], name=row[1], length_m=row[2]) if row else None

    def get_all(self) -> list[Track]:
        rows = self._conn.execute(
            "SELECT id, name, length_m FROM tracks ORDER BY name ASC"
        ).fetchall()
        return [Track(id=r[0], name=r[1], length_m=r[2]) for r in rows]

    def rename(self, track_id: int, new_name: str) -> int:
        """Renomeia a pista, **preservando as voltas**. Devolve o id final.

        Existe porque um nome errado é uma sujeira que se espalha e não sai
        sozinha: uma sessão gravada sob "192.168.15.156" — o IP do PS5 digitado
        no campo de pista — deixa esse rótulo em todo seletor do programa, e a
        única alternativa antes disto era apagar as voltas.

        Se já existe uma pista com o nome de destino, as voltas **migram** para
        ela e a duplicata é removida. É o caso que mais importa na prática:
        quem digitou o IP quase sempre também tem a pista certa gravada, e
        recusar a operação por "nome já existe" deixaria o acervo partido em
        dois exatamente onde se pediu para juntá-lo.
        """
        clean = new_name.strip()
        if not clean:
            raise ValueError("nome de pista vazio")

        with self._db.lock:
            existente = self._conn.execute(
                "SELECT id FROM tracks WHERE name = ? AND id != ?", (clean, track_id)
            ).fetchone()

            if existente is None:
                self._conn.execute(
                    "UPDATE tracks SET name = ? WHERE id = ?", (clean, track_id)
                )
                final_id = track_id
            else:
                final_id = int(existente[0])
                self._conn.execute(
                    "UPDATE laps SET track_id = ? WHERE track_id = ?",
                    (final_id, track_id),
                )
                self._conn.execute(
                    "UPDATE sessions SET track_id = ? WHERE track_id = ?",
                    (final_id, track_id),
                )
                self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._conn.commit()

        return final_id

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
