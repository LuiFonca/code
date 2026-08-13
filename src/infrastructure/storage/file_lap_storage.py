"""
Implementação em arquivo JSON de `LapRepository` — esqueleto.

Ainda não implementada: o armazenamento em produção é o SQLite
(`repositories/sqlite_lap_repository.py`). Esta classe existe para fixar o
ponto de extensão previsto na arquitetura — exportar/importar voltas como
arquivo, útil para backup e para compartilhar uma volta com outra pessoa.

As assinaturas já estão completas e corretas; só os corpos faltam. Assim, quando
for implementada, nada além deste arquivo precisa mudar: o composition root
troca `SqliteLapRepository` por `FileLapStorage` e o resto do app não percebe.
"""

import json
from pathlib import Path

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint


class FileLapStorage(LapRepository):
    """Voltas persistidas como JSON num diretório."""

    def __init__(self, storage_dir: Path | str):
        self._dir = Path(storage_dir)

    def save(self, lap: Lap) -> int:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_by_id(self, lap_id: int) -> Lap | None:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_all(self, limit: int | None = None) -> list[Lap]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_best(self, track_id: int) -> Lap | None:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_top(self, track_id: int, limit: int = 5) -> list[Lap]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def load_points(self, lap_id: int) -> list[TelemetryPoint]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_sector_times(self, lap_id: int) -> list[int | None]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def get_sector_times_batch(self, lap_ids: list[int]) -> dict[int, list[int | None]]:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def delete(self, lap_id: int) -> None:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")

    def delete_by_track(self, track_id: int) -> None:
        raise NotImplementedError("Armazenamento em JSON ainda não implementado.")
