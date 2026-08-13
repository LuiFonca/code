"""Catálogo de pistas do jogo (somente leitura) — inclui a auto-identificação."""

from ...domain.interfaces.track_repository import TrackRepository
from ...domain.models.track import Track
from .csv_catalog import CsvCatalog


class CsvTrackRepository(TrackRepository):
    """As ~105 pistas do GT7, com comprimento — o que viabiliza adivinhar a
    pista em uso a partir da distância medida na volta."""

    def __init__(self, catalog: CsvCatalog):
        self._catalog = catalog

    def get_all(self) -> list[Track]:
        return sorted(self._catalog.tracks.values(), key=lambda t: t.name)

    def get_by_id(self, track_id: int) -> Track | None:
        return self._catalog.tracks.get(track_id)

    def find_by_name(self, name: str) -> list[Track]:
        needle = name.strip().lower()
        if not needle:
            return []
        return sorted(
            (t for t in self._catalog.tracks.values() if needle in t.name.lower()),
            key=lambda t: t.name,
        )

    def guess_by_length(
        self, lap_distance_m: float, tolerance_pct: float = 5.0
    ) -> list[Track]:
        """Pistas cujo comprimento cai dentro de `tolerance_pct`% da distância
        medida, ordenadas da mais próxima para a mais distante.

        Costuma devolver mais de uma: circuitos de comprimento parecido são
        indistinguíveis só pela distância, e a distância medida ainda carrega o
        erro de integrar velocidade a 60 Hz. Por isso a UI oferece os candidatos
        em vez de assumir o primeiro.
        """
        tracks = self._catalog.tracks
        if not tracks or lap_distance_m <= 0:
            return []
        threshold = lap_distance_m * (tolerance_pct / 100)
        candidates = [
            t
            for t in tracks.values()
            if t.length_m is not None and abs(t.length_m - lap_distance_m) <= threshold
        ]
        candidates.sort(key=lambda t: abs(t.length_m - lap_distance_m))
        return candidates

    def get_or_create(self, name: str) -> int:
        raise NotImplementedError(
            "O catálogo do jogo é somente leitura. Use SqliteTrackRepository "
            "para registrar pistas usadas pelo piloto."
        )
