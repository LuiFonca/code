"""
Catálogo estático de carros, fabricantes e pistas do Gran Turismo 7,
carregado a partir de arquivos CSV.

Uso futuro: auto-detecção de pista pela distância total da volta
(comparando com o campo Length do CSV), e identificação de carro pelo
car_id do pacote de telemetria (quando disponível).

Os CSVs são embarcados junto com o app em data/ — os dados vêm da
comunidade GT7 e não são oficiais da Polyphony Digital.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class CarInfo:
    car_id: int
    short_name: str
    maker_id: int


@dataclass
class MakerInfo:
    maker_id: int
    name: str


@dataclass
class TrackInfo:
    track_id: int
    name: str
    length_m: float
    country_id: int
    num_corners: int
    is_oval: bool
    is_reverse: bool


_cars: dict[int, CarInfo] | None = None
_makers: dict[int, MakerInfo] | None = None
_tracks: dict[int, TrackInfo] | None = None


def _load_cars():
    global _cars
    if _cars is not None:
        return
    _cars = {}
    path = DATA_DIR / "cars.csv"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = int(row["ID"])
            _cars[cid] = CarInfo(
                car_id=cid,
                short_name=row["ShortName"],
                maker_id=int(row["Maker"]),
            )


def _load_makers():
    global _makers
    if _makers is not None:
        return
    _makers = {}
    path = DATA_DIR / "makers.csv"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = int(row["Maker"])
            _makers[mid] = MakerInfo(maker_id=mid, name=row["Name"])


def _load_tracks():
    global _tracks
    if _tracks is not None:
        return
    _tracks = {}
    path = DATA_DIR / "track_list.csv"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = int(row["ID"])
            _tracks[tid] = TrackInfo(
                track_id=tid,
                name=row["Name"],
                length_m=float(row["Length"]),
                country_id=int(row.get("Country", 0)),
                num_corners=int(row.get("NumCorners", 0)),
                is_oval=row.get("IsOval", "0") == "1",
                is_reverse=row.get("IsReverse", "0") == "1",
            )


def get_car(car_id: int) -> CarInfo | None:
    _load_cars()
    return _cars.get(car_id)


def get_car_full_name(car_id: int) -> str | None:
    car = get_car(car_id)
    if car is None:
        return None
    _load_makers()
    maker = _makers.get(car.maker_id)
    maker_name = maker.name if maker else ""
    return f"{maker_name} {car.short_name}".strip()


def get_maker(maker_id: int) -> MakerInfo | None:
    _load_makers()
    return _makers.get(maker_id)


def get_track(track_id: int) -> TrackInfo | None:
    _load_tracks()
    return _tracks.get(track_id)


def guess_track_by_length(lap_distance_m: float, tolerance_pct: float = 5.0) -> list[TrackInfo]:
    """Retorna pistas candidatas cuja Length esteja dentro de tolerance_pct%
    da distância percorrida na volta. Útil para sugerir qual pista o
    jogador está usando baseado apenas na telemetria."""
    _load_tracks()
    if not _tracks or lap_distance_m <= 0:
        return []
    threshold = lap_distance_m * (tolerance_pct / 100)
    candidates = []
    for t in _tracks.values():
        if abs(t.length_m - lap_distance_m) <= threshold:
            candidates.append(t)
    candidates.sort(key=lambda t: abs(t.length_m - lap_distance_m))
    return candidates


def all_tracks() -> list[TrackInfo]:
    _load_tracks()
    return sorted(_tracks.values(), key=lambda t: t.name) if _tracks else []


def all_makers() -> list[MakerInfo]:
    _load_makers()
    return sorted(_makers.values(), key=lambda m: m.name) if _makers else []
