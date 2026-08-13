"""
Carros efetivamente usados pelo piloto, gravados no banco.

Complementa o catálogo estático (`CsvCarRepository`): aqui ficam só os carros
que apareceram em alguma volta, incluindo nomes digitados à mão que não existem
no CSV do jogo.
"""

import time

from ...domain.interfaces.car_repository import CarRepository
from ...domain.models.car import Car
from .sqlite_database import UNKNOWN_CAR_NAME, SqliteDatabase


class SqliteCarRepository(CarRepository):
    def __init__(self, database: SqliteDatabase):
        self._db = database

    @property
    def _conn(self):
        return self._db.connection

    def get_all(self) -> list[Car]:
        rows = self._conn.execute("SELECT id, name FROM cars ORDER BY name ASC").fetchall()
        return [Car(id=r[0], name=r[1]) for r in rows]

    def get_by_id(self, car_id: int) -> Car | None:
        row = self._conn.execute(
            "SELECT id, name FROM cars WHERE id = ?", (car_id,)
        ).fetchone()
        return Car(id=row[0], name=row[1]) if row else None

    def find_by_name(self, name: str) -> list[Car]:
        rows = self._conn.execute(
            "SELECT id, name FROM cars WHERE name LIKE ? ORDER BY name ASC",
            (f"%{name}%",),
        ).fetchall()
        return [Car(id=r[0], name=r[1]) for r in rows]

    def get_or_create(self, name: str) -> int:
        """Id do carro, criando o registro se for a primeira vez.

        Nome vazio vira "Desconhecido" — diferente da pista, um carro não
        identificado **não** impede a gravação da volta.
        """
        name = (name or "").strip() or UNKNOWN_CAR_NAME
        with self._db.lock:
            row = self._conn.execute(
                "SELECT id FROM cars WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return row[0]
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO cars (name, created_at) VALUES (?, ?)", (name, time.time())
            )
            self._conn.commit()
            return cur.lastrowid
