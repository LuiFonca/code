"""
Pistas já utilizadas pelo piloto, gravadas no banco.

Complementa o catálogo estático (`CsvTrackRepository`). A interface pede
`guess_by_length`, mas identificar pista por comprimento é trabalho do catálogo
— aqui só existem as pistas que o usuário já usou, e o comprimento nem sempre
é conhecido. Ver o método para o comportamento adotado.
"""

import time

from ...domain.interfaces.track_repository import TrackRepository
from ...domain.models.track import Track
from .sqlite_database import UNKNOWN_TRACK_NAME, SqliteDatabase


class SqliteTrackRepository(TrackRepository):
    def __init__(self, database: SqliteDatabase):
        self._db = database

    @property
    def _conn(self):
        return self._db.connection

    @staticmethod
    def _row_to_track(row) -> Track:
        track_id, name, raw = row
        fractions = None
        if raw:
            try:
                fractions = [float(x) for x in str(raw).split(",") if x.strip()]
            except ValueError:
                fractions = None
        return Track(id=track_id, name=name, sector_fractions=fractions)

    def get_all(self) -> list[Track]:
        rows = self._conn.execute(
            "SELECT id, name, sector_fractions FROM tracks ORDER BY name ASC"
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def get_by_id(self, track_id: int) -> Track | None:
        row = self._conn.execute(
            "SELECT id, name, sector_fractions FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return self._row_to_track(row) if row else None

    def find_by_name(self, name: str) -> list[Track]:
        rows = self._conn.execute(
            "SELECT id, name, sector_fractions FROM tracks WHERE name LIKE ? "
            "ORDER BY name ASC",
            (f"%{name}%",),
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def set_sector_fractions(
        self, track_id: int, fractions: list[float] | None
    ) -> None:
        """Define onde caem os limites de setor desta pista.

        Frações da distância total, crescentes, terminando em 1.0 (ex.:
        `[0.31, 0.68, 1.0]`). `None` volta à divisão em partes iguais.

        Só afeta voltas gravadas **a partir daí** — os tempos de setor são
        calculados uma vez, na gravação. Recalcular o histórico inteiro seria
        possível, mas mudaria silenciosamente números que o usuário já viu.
        """
        value = None
        if fractions:
            value = ",".join(f"{f:g}" for f in fractions)
        with self._db.lock:
            self._conn.execute(
                "UPDATE tracks SET sector_fractions = ? WHERE id = ?",
                (value, track_id),
            )
            self._conn.commit()

    def guess_by_length(
        self, lap_distance_m: float, tolerance_pct: float = 5.0
    ) -> list[Track]:
        """Sempre vazio: o banco não guarda comprimento de pista.

        A auto-identificação por comprimento é responsabilidade do catálogo CSV,
        que tem `length_m`. Retornar vazio (em vez de levantar) mantém o
        repositório utilizável de forma intercambiável com o catálogo.
        """
        return []

    def list_with_lap_count(self) -> list[tuple[int, str, int]]:
        """(id, nome, nº de voltas) por pista — alimenta o seletor de pistas,
        que mostra quantas voltas já existem em cada uma."""
        rows = self._conn.execute("""
            SELECT tracks.id, tracks.name, COUNT(laps.id) AS lap_count
            FROM tracks
            LEFT JOIN laps ON laps.track_id = tracks.id AND laps.is_player = 1
            GROUP BY tracks.id
            ORDER BY tracks.name ASC
        """).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_or_create(self, name: str) -> int:
        name = (name or "").strip() or UNKNOWN_TRACK_NAME
        with self._db.lock:
            row = self._conn.execute(
                "SELECT id FROM tracks WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return row[0]
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO tracks (name, created_at) VALUES (?, ?)",
                (name, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def delete(self, track_id: int) -> None:
        """Remove a pista e, em cascata, tudo que dependia dela."""
        with self._db.lock:
            self._conn.execute("DELETE FROM laps WHERE track_id = ?", (track_id,))
            self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._conn.commit()
