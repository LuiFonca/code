"""
O catálogo do jogo: o que a telemetria **não** diz.

O pacote UDP do GT7 traz um `car_id` numérico e nada sobre a pista. Nem nome do
carro, nem nome do circuito, nem comprimento. Sem uma tabela externa, uma volta
gravada é "carro 24 em algum lugar" — o que torna o histórico ilegível e a
comparação entre pistas impossível de conferir.

Este módulo é essa tabela: 527 carros, 72 montadoras e 105 circuitos, com
comprimento, número de curvas e as marcas de oval e traçado invertido.

A identificação de pista merece explicação
------------------------------------------
Como o jogo não informa onde o piloto está, a única pista disponível é o
**comprimento medido da volta** — que o motor já calcula integrando velocidade.
`guess_by_length` compara essa distância com o catálogo e devolve os candidatos
ordenados por proximidade.

Devolve uma **lista**, e não o melhor palpite, de propósito. Circuitos de
comprimento parecido são indistinguíveis só pela distância, e a medida ainda
carrega o erro de integrar a 60 Hz. Escolher sozinho acertaria na maioria das
vezes e gravaria a volta na pista errada no resto — e uma volta arquivada sob o
circuito errado contamina a comparação para sempre, silenciosamente. Oferecer os
candidatos deixa a decisão com quem estava dirigindo e sabe a resposta.

Origem dos dados
----------------
Os CSVs vêm da comunidade do GT7, não da Polyphony Digital. Podem envelhecer a
cada atualização do jogo; um id desconhecido devolve `None` em vez de erro,
então carro novo aparece sem nome e nada mais quebra.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..domain.models import Track
from ..observability.logging import get_logger

_log = get_logger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"

# Abaixo disto a distância acumulada não é uma volta inteira — é uma saída dos
# boxes ou uma volta abortada, e palpitar sobre ela produziria lixo.
MIN_DISTANCE_FOR_GUESS_M = 500.0

DEFAULT_TOLERANCE_PCT = 5.0


@dataclass(frozen=True, slots=True)
class CatalogTrack:
    """Um circuito do catálogo.

    Separado de `domain.models.Track` de propósito: aquele representa a pista
    **que o piloto usou** e vive no banco com id próprio; este é a ficha
    somente-leitura vinda do jogo, com id do jogo. Fundir os dois faria o id
    significar duas coisas diferentes conforme a origem.
    """

    game_id: int
    name: str
    length_m: float | None = None
    country: str = ""
    corners: int = 0
    is_oval: bool = False
    is_reverse: bool = False

    def as_track(self) -> Track:
        """A forma que o resto do sistema usa. Sem `id`: quem grava atribui."""
        return Track(name=self.name, length_m=self.length_m)


@dataclass(frozen=True, slots=True)
class CatalogCar:
    game_id: int
    name: str
    maker_id: int | None = None


class GameCatalog:
    """Carros, montadoras e circuitos do GT7, lidos dos CSVs embarcados.

    A carga é preguiçosa: são ~700 linhas, mas nada disso é necessário até
    alguém perguntar, e o núcleo sobe para um teste unitário sem tocar em disco.

    CSV ausente resulta em catálogo **vazio**, não em exceção. A aplicação
    continua utilizável com identificação manual — que é exatamente como ela
    funciona sem catálogo nenhum.
    """

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR) -> None:
        self._data_dir = Path(data_dir)
        self._cars: dict[int, CatalogCar] | None = None
        self._makers: dict[int, str] | None = None
        self._tracks: dict[int, CatalogTrack] | None = None

    # ---------- acesso ----------

    @property
    def cars(self) -> dict[int, CatalogCar]:
        if self._cars is None:
            self._cars = self._load_cars()
        return self._cars

    @property
    def makers(self) -> dict[int, str]:
        if self._makers is None:
            self._makers = self._load_makers()
        return self._makers

    @property
    def tracks(self) -> dict[int, CatalogTrack]:
        if self._tracks is None:
            self._tracks = self._load_tracks()
        return self._tracks

    @property
    def is_empty(self) -> bool:
        return not self.cars and not self.tracks

    # ---------- consultas ----------

    def car_name(self, car_id: int) -> str | None:
        """`24` vira `"Nissan 180SX Type X '96"`. None se o id for desconhecido.

        É o que transforma o histórico de "carro 24" em algo legível — e o que
        preenche o campo `car` do debrief do engenheiro.
        """
        car = self.cars.get(car_id)
        if car is None:
            return None

        maker = self.makers.get(car.maker_id) if car.maker_id is not None else None
        # `split()` e não `strip()`: vários `ShortName` do CSV vêm com espaço à
        # esquerda, e concatenar produziria "Ford  GT '06" com espaço duplo.
        return " ".join(f"{maker or ''} {car.name}".split())

    def car_maker(self, car_id: int) -> str | None:
        car = self.cars.get(car_id)
        if car is None or car.maker_id is None:
            return None
        return self.makers.get(car.maker_id)

    # Não existe aqui um `car(id) -> Car` que devolva o modelo do domínio já
    # montado, e a ausência é deliberada. A primeira versão tinha, com
    # `Car(id=car_id, ...)` — e o id do **jogo** foi parar na coluna que é chave
    # estrangeira para a tabela local de carros. Toda volta falhava ao gravar
    # com `FOREIGN KEY constraint failed`, e o erro só aparecia no log.
    #
    # É o mesmo perigo que separa `CatalogTrack` de `domain.Track`: o catálogo
    # sabe **nomes**, e quem tem id é o banco. Expor só o nome obriga quem chama
    # a passar por `get_or_create`, que é onde o id local nasce.

    def find_tracks(self, needle: str) -> list[CatalogTrack]:
        """Busca por nome, sem diferenciar maiúsculas. Para a caixa de busca."""
        query = needle.strip().lower()
        if not query:
            return []
        return sorted(
            (t for t in self.tracks.values() if query in t.name.lower()),
            key=lambda t: t.name,
        )

    def guess_by_length(
        self,
        lap_distance_m: float,
        *,
        tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
        limit: int = 5,
    ) -> list[CatalogTrack]:
        """Circuitos compatíveis com a distância medida, do mais próximo ao menos.

        Lista, e não palpite único — ver o cabeçalho do módulo.
        """
        if lap_distance_m < MIN_DISTANCE_FOR_GUESS_M:
            return []

        threshold = lap_distance_m * (tolerance_pct / 100.0)
        candidates = [
            track
            for track in self.tracks.values()
            if track.length_m is not None
            and abs(track.length_m - lap_distance_m) <= threshold
        ]
        candidates.sort(key=lambda t: abs((t.length_m or 0.0) - lap_distance_m))
        return candidates[:limit]

    # ---------- carga ----------

    def _load_cars(self) -> dict[int, CatalogCar]:
        result: dict[int, CatalogCar] = {}
        for row in self._read("cars.csv"):
            try:
                game_id = int(row["ID"])
            except (KeyError, ValueError):
                continue
            maker_id: int | None
            try:
                maker_id = int(row["Maker"])
            except (KeyError, ValueError):
                maker_id = None
            result[game_id] = CatalogCar(
                game_id=game_id, name=row.get("ShortName", "").strip(), maker_id=maker_id
            )
        return result

    def _load_makers(self) -> dict[int, str]:
        result: dict[int, str] = {}
        for row in self._read("makers.csv"):
            try:
                result[int(row["Maker"])] = row.get("Name", "").strip()
            except (KeyError, ValueError):
                continue
        return result

    def _load_tracks(self) -> dict[int, CatalogTrack]:
        result: dict[int, CatalogTrack] = {}
        for row in self._read("track_list.csv"):
            try:
                game_id = int(row["ID"])
            except (KeyError, ValueError):
                continue
            result[game_id] = CatalogTrack(
                game_id=game_id,
                name=row.get("Name", "").strip(),
                length_m=_as_float(row.get("Length")),
                country=row.get("Country", "").strip(),
                corners=int(_as_float(row.get("NumCorners")) or 0),
                is_oval=row.get("IsOval") == "1",
                is_reverse=row.get("IsReverse") == "1",
            )
        return result

    def _read(self, filename: str) -> list[dict[str, str]]:
        path = self._data_dir / filename
        if not path.is_file():
            # Sem alarde: catálogo ausente é um modo de operação, não uma falha.
            _log.info("catálogo não encontrado: %s", path)
            return []
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


def _as_float(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
