"""
Catálogo estático do GT7 lido dos CSVs embarcados.

Os três repositórios de catálogo (carros, montadoras, pistas) compartilham esta
classe porque `CsvCarRepository.get_full_name()` precisa cruzar carro com
montadora — mantê-los em caches separados exigiria que um conhecesse o outro.

Diferença em relação ao módulo antigo (`analysis/gt7_catalog.py`): os caches
eram globais de módulo (`_cars`, `_makers`, `_tracks`), o que impedia testar com
dados alternativos sem contaminar o processo inteiro. Agora são atributos de
instância e o diretório vem pelo construtor.

Os dados vêm da comunidade GT7, não da Polyphony Digital.
"""

import csv
from pathlib import Path

from ...domain.models.car import Car
from ...domain.models.maker import Maker
from ...domain.models.track import Track

# Fica dentro de src/ para o pacote ser autossuficiente: apontar para a pasta
# do app antigo faria o catálogo (527 carros, 105 pistas, auto-identificação de
# pista) sumir no dia em que aquela pasta fosse removida.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class CsvCatalog:
    """Carrega e mantém em memória o catálogo do jogo.

    A carga é preguiçosa: são ~700 linhas no total, mas nada disso é necessário
    até que alguém pergunte. Um CSV ausente resulta em catálogo vazio em vez de
    exceção — o app continua utilizável com identificação manual, que é como ele
    funcionava antes de os CSVs existirem.
    """

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        self._data_dir = Path(data_dir)
        self._cars: dict[int, Car] | None = None
        self._makers: dict[int, Maker] | None = None
        self._tracks: dict[int, Track] | None = None

    @property
    def cars(self) -> dict[int, Car]:
        if self._cars is None:
            self._cars = self._load_cars()
        return self._cars

    @property
    def makers(self) -> dict[int, Maker]:
        if self._makers is None:
            self._makers = self._load_makers()
        return self._makers

    @property
    def tracks(self) -> dict[int, Track]:
        if self._tracks is None:
            self._tracks = self._load_tracks()
        return self._tracks

    def car_full_name(self, car_id: int) -> str | None:
        """Nome completo "Montadora Modelo" — é o que a auto-detecção de carro
        exibe. None se o id não estiver no catálogo."""
        car = self.cars.get(car_id)
        if car is None:
            return None
        maker = self.makers.get(car.maker_id) if car.maker_id is not None else None
        # `split()` em vez de `strip()`: vários ShortName no CSV vêm com espaço
        # à esquerda, o que produziria "Ford  GT '06" com espaço duplo.
        return " ".join(f"{maker.name if maker else ''} {car.name}".split())

    # ---------- carga ----------

    def _load_cars(self) -> dict[int, Car]:
        rows = self._read_csv("cars.csv")
        result: dict[int, Car] = {}
        for row in rows:
            try:
                cid = int(row["ID"])
                result[cid] = Car(
                    id=cid,
                    name=row["ShortName"],
                    maker_id=int(row["Maker"]),
                )
            except (KeyError, ValueError):
                # Linha malformada não pode derrubar o catálogo inteiro.
                continue
        return result

    def _load_makers(self) -> dict[int, Maker]:
        rows = self._read_csv("makers.csv")
        result: dict[int, Maker] = {}
        for row in rows:
            try:
                mid = int(row["Maker"])
                result[mid] = Maker(id=mid, name=row["Name"])
            except (KeyError, ValueError):
                continue
        return result

    def _load_tracks(self) -> dict[int, Track]:
        rows = self._read_csv("track_list.csv")
        result: dict[int, Track] = {}
        for row in rows:
            try:
                tid = int(row["ID"])
                result[tid] = Track(
                    id=tid,
                    name=row["Name"],
                    length_m=float(row["Length"]),
                    location=row.get("Country") or None,
                    corners=int(row.get("NumCorners", 0) or 0),
                    is_oval=row.get("IsOval", "0") == "1",
                    is_reverse=row.get("IsReverse", "0") == "1",
                )
            except (KeyError, ValueError):
                continue
        return result

    def _read_csv(self, filename: str) -> list[dict]:
        path = self._data_dir / filename
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
