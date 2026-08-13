"""
Voltas em arquivo JSON.

Serve a dois propósitos: exportar uma volta para backup ou para mandar a outra
pessoa, e ser a segunda implementação de `LapRepository` — o que torna o
contrato do repositório verificável de verdade, em vez de teórico.

Formato: um arquivo por volta, `lap-<id>.json`, num diretório. Simples de
propósito — quem recebe um arquivo destes consegue abrir e ler sem o app.

Sobre desempenho: as consultas de listagem varrem o diretório. Com dezenas de
voltas isso é irrelevante; com milhares, seria. O armazenamento de produção
continua sendo o SQLite, e esta implementação existe para troca de arquivos,
não para substituir o banco.
"""

import json
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint

# Versão do formato. Um arquivo de versão desconhecida é recusado com mensagem
# clara em vez de produzir amostras truncadas silenciosamente.
FORMAT_VERSION = 1

_PONTO_CAMPOS = tuple(f.name for f in fields(TelemetryPoint))


class UnsupportedLapFile(ValueError):
    """Arquivo de volta ilegível ou de versão futura."""


class FileLapStorage(LapRepository):
    """Implementação de `LapRepository` sobre arquivos JSON."""

    def __init__(self, storage_dir: Path | str, num_sectors: int = 3):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._num_sectors = num_sectors

    # ------------------------------------------------------------ caminhos

    def _path(self, lap_id: int) -> Path:
        return self._dir / f"lap-{lap_id}.json"

    def _next_id(self) -> int:
        """Maior id existente + 1.

        Varre o diretório em vez de manter contador em arquivo: contador
        separado sai de sincronia assim que alguém copia um arquivo para dentro
        da pasta à mão, que é justamente o caso de uso de importação.
        """
        maior = 0
        for arquivo in self._dir.glob("lap-*.json"):
            try:
                maior = max(maior, int(arquivo.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return maior + 1

    # ------------------------------------------------------------ serialização

    @staticmethod
    def _lap_to_dict(lap: Lap) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "id": lap.id,
            "track_id": lap.track_id,
            "car_id": lap.car_id,
            "lap_time_ms": lap.lap_time_ms,
            "start_time": lap.start_time.isoformat() if lap.start_time else None,
            "end_time": lap.end_time.isoformat() if lap.end_time else None,
            "is_player": lap.is_player,
            "is_complete": lap.is_complete,
            "is_valid": lap.is_valid,
            # Lista de listas em vez de lista de objetos: com milhares de
            # amostras, repetir 27 nomes de campo por ponto multiplicaria o
            # tamanho do arquivo por seis sem ganhar legibilidade real.
            "point_fields": list(_PONTO_CAMPOS),
            "points": [
                [getattr(p, campo) for campo in _PONTO_CAMPOS] for p in lap.points
            ],
        }

    @staticmethod
    def _dict_to_lap(dados: dict, *, with_points: bool = True) -> Lap:
        versao = dados.get("format_version")
        if versao is None or versao > FORMAT_VERSION:
            raise UnsupportedLapFile(
                f"Arquivo de volta em formato não suportado (versão {versao!r}). "
                f"Esta versão do app lê até a {FORMAT_VERSION}."
            )

        pontos: list[TelemetryPoint] = []
        if with_points:
            # Os nomes vêm do arquivo: uma volta exportada por versão anterior
            # pode ter menos campos, e os ausentes ficam None em vez de
            # desalinhar tudo a partir do primeiro campo novo.
            nomes = dados.get("point_fields") or list(_PONTO_CAMPOS)
            for linha in dados.get("points", []):
                valores = dict(zip(nomes, linha))
                pontos.append(
                    TelemetryPoint(
                        **{campo: valores.get(campo) for campo in _PONTO_CAMPOS}
                    )
                )

        def _quando(chave):
            bruto = dados.get(chave)
            return datetime.fromisoformat(bruto) if bruto else None

        return Lap(
            id=dados.get("id"),
            track_id=dados.get("track_id"),
            car_id=dados.get("car_id"),
            lap_time_ms=dados.get("lap_time_ms", 0),
            start_time=_quando("start_time"),
            end_time=_quando("end_time"),
            is_player=bool(dados.get("is_player", True)),
            is_complete=bool(dados.get("is_complete", True)),
            is_valid=bool(dados.get("is_valid", True)),
            points=pontos,
        )

    def _read(self, lap_id: int, *, with_points: bool = True) -> Lap | None:
        caminho = self._path(lap_id)
        if not caminho.exists():
            return None
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise UnsupportedLapFile(
                f"Não foi possível ler {caminho.name}: arquivo corrompido."
            ) from exc
        return self._dict_to_lap(dados, with_points=with_points)

    def _all(self, *, with_points: bool = False) -> list[Lap]:
        voltas = []
        for arquivo in self._dir.glob("lap-*.json"):
            try:
                lap_id = int(arquivo.stem.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            try:
                lap = self._read(lap_id, with_points=with_points)
            except UnsupportedLapFile:
                # Um arquivo estranho na pasta não pode derrubar a listagem
                # inteira — o resto do histórico continua acessível.
                continue
            if lap is not None:
                voltas.append(lap)
        return voltas

    # ------------------------------------------------------------ escrita

    def save(self, lap: Lap) -> int:
        lap_id = lap.id if lap.id is not None else self._next_id()
        gravada = Lap(
            id=lap_id, track_id=lap.track_id, car_id=lap.car_id,
            lap_time_ms=lap.lap_time_ms,
            start_time=lap.start_time or datetime.now(),
            end_time=lap.end_time, is_player=lap.is_player,
            is_complete=lap.is_complete, is_valid=lap.is_valid, points=lap.points,
        )
        caminho = self._path(lap_id)
        # Grava em temporário e renomeia: interromper no meio deixaria um JSON
        # truncado que a leitura recusaria como corrompido.
        temporario = caminho.with_suffix(".json.tmp")
        temporario.write_text(
            json.dumps(self._lap_to_dict(gravada), ensure_ascii=False),
            encoding="utf-8",
        )
        temporario.replace(caminho)
        self._write_sectors(lap_id, gravada)
        return lap_id

    def _write_sectors(self, lap_id: int, lap: Lap) -> None:
        """Calcula e grava os setores junto da volta.

        Mesma divisão por distância usada no SQLite. Sem os pontos oficiais do
        GT7, a volta é cortada em trechos iguais.
        """
        if not lap.points:
            return
        total = lap.points[-1].distance_m
        if total <= 0:
            return

        limites = [total * (i / self._num_sectors) for i in range(1, self._num_sectors + 1)]
        tempos: list[int] = []
        ultimo_ms = lap.points[0].elapsed_ms
        indice = 0
        for ponto in lap.points:
            if indice >= self._num_sectors:
                break
            if ponto.distance_m >= limites[indice]:
                tempos.append(ponto.elapsed_ms - ultimo_ms)
                ultimo_ms = ponto.elapsed_ms
                indice += 1
        if len(tempos) < self._num_sectors:
            tempos.append(lap.points[-1].elapsed_ms - ultimo_ms)

        (self._dir / f"sectors-{lap_id}.json").write_text(
            json.dumps(tempos), encoding="utf-8"
        )

    def set_valid(self, lap_id: int, is_valid: bool) -> None:
        lap = self._read(lap_id)
        if lap is None:
            return
        lap.is_valid = is_valid
        self.save(lap)

    def delete(self, lap_id: int) -> None:
        self._path(lap_id).unlink(missing_ok=True)
        (self._dir / f"sectors-{lap_id}.json").unlink(missing_ok=True)

    def delete_by_track(self, track_id: int) -> None:
        for lap in self._all():
            if lap.track_id == track_id and lap.id is not None:
                self.delete(lap.id)

    # ------------------------------------------------------------ leitura

    def get_by_id(self, lap_id: int) -> Lap | None:
        try:
            return self._read(lap_id, with_points=True)
        except UnsupportedLapFile:
            return None

    def get_all(self, limit: int | None = None) -> list[Lap]:
        voltas = [lap for lap in self._all() if lap.is_player]
        voltas.sort(key=lambda lap: (lap.start_time or datetime.min), reverse=True)
        return voltas[:limit] if limit else voltas

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        voltas = [
            lap for lap in self._all() if lap.is_player and lap.track_id == track_id
        ]
        voltas.sort(key=lambda lap: (lap.start_time or datetime.min), reverse=True)
        return voltas[:limit] if limit else voltas

    def _elegiveis(self, track_id: int) -> list[Lap]:
        """Voltas que disputam recorde: do piloto, completas e válidas."""
        return [
            lap
            for lap in self._all()
            if lap.track_id == track_id
            and lap.is_player
            and lap.is_complete
            and lap.is_valid
            and lap.lap_time_ms > 0
        ]

    def get_best(self, track_id: int) -> Lap | None:
        elegiveis = self._elegiveis(track_id)
        if not elegiveis:
            return None
        # Desempate pelo id mais antigo, igual ao SQLite — o critério tem que
        # ser o mesmo nas duas implementações para o troféu do histórico não
        # discordar da referência do delta.
        return min(elegiveis, key=lambda lap: (lap.lap_time_ms, lap.id or 0))

    def get_top(self, track_id: int, limit: int = 5) -> list[Lap]:
        elegiveis = self._elegiveis(track_id)
        elegiveis.sort(key=lambda lap: (lap.lap_time_ms, lap.id or 0))
        return elegiveis[:limit]

    def load_points(self, lap_id: int) -> list[TelemetryPoint]:
        lap = self.get_by_id(lap_id)
        return lap.points if lap else []

    def get_sector_times(self, lap_id: int) -> list[int | None]:
        caminho = self._dir / f"sectors-{lap_id}.json"
        if not caminho.exists():
            return []
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def get_sector_times_batch(self, lap_ids: list[int]) -> dict[int, list[int | None]]:
        return {lap_id: self.get_sector_times(lap_id) for lap_id in lap_ids}

    # ------------------------------------------------------- importar/exportar

    def export_lap(self, lap: Lap, destino: Path | str) -> Path:
        """Grava a volta num arquivo avulso, fora do diretório do repositório."""
        caminho = Path(destino)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(self._lap_to_dict(lap), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return caminho

    @classmethod
    def read_lap_file(cls, origem: Path | str) -> Lap:
        """Lê uma volta de um arquivo avulso.

        Levanta `UnsupportedLapFile` com mensagem legível quando o arquivo está
        corrompido ou veio de uma versão mais nova do formato — o usuário
        precisa saber o que aconteceu, não ver um traceback de JSON.
        """
        caminho = Path(origem)
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise UnsupportedLapFile(
                f"Não foi possível ler {caminho.name}: o arquivo não é um JSON válido."
            ) from exc
        if not isinstance(dados, dict):
            raise UnsupportedLapFile(f"{caminho.name} não contém uma volta.")
        return cls._dict_to_lap(dados)
