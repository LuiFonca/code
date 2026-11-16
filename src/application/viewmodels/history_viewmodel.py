"""
Estado da tela "Histórico".
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.services.lap_export import lap_to_csv
from ...domain.services.session_stats import IdealLap, ideal_lap
from ..events.event_bus import EventBus
from ..events.events import LapCompleted, LapDeleted, LapsPurged


@dataclass(slots=True)
class LapRow:
    """Uma linha da tabela, já pronta para exibir.

    A View não deve consultar repositório nem formatar tempo de setor: recebe
    isto montado. Na versão antiga a aba de histórico chamava `lap_storage`
    diretamente, o que colocava SQL dentro do widget.
    """

    lap: Lap
    car_name: str | None
    sector_times: list[int | None]
    is_best: bool
    # Volta observada só em parte (app conectado com ela já em andamento). O
    # tempo é verdadeiro, mas os dados cobrem um pedaço — por isso ela nunca
    # é recorde, e a tabela precisa dizer isso em vez de deixar o usuário
    # achar que o app perdeu o recorde dele.
    is_complete: bool = True
    # False quando o piloto marcou a volta como inválida (corte, contato).
    is_valid: bool = True
    # A volta tem anotação ou acerto registrado — a tabela marca com um ícone.
    has_notes: bool = False


class HistoryViewModel(QObject):
    """Lista de voltas de uma pista, com busca e exclusão."""

    laps_changed = Signal(list)     # list[LapRow]
    error = Signal(str)

    def __init__(
        self,
        lap_repository: LapRepository,
        event_bus: EventBus,
        track_repository=None,
        track_identifier=None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._laps = lap_repository
        self._bus = event_bus
        # Opcional: aprende o traçado quando o usuário atribui a pista à mão.
        self._identifier = track_identifier
        # Opcional: só é usado para atribuir pista a uma volta que ficou sem.
        self._tracks = track_repository
        self._track_id: int | None = None
        self.ideal = IdealLap(total_ms=None)
        self._filter = ""
        self._rows: list[LapRow] = []

        # Volta nova gravada = tabela desatualizada. Recarregar por evento evita
        # que a View precise saber quando pedir refresh. A poda por retenção
        # também muda a tabela, e antes não avisava ninguém.
        self._on_lap_completed = lambda _e: self.refresh()
        self._on_laps_purged = lambda _e: self.refresh()
        self._bus.subscribe(LapCompleted, self._on_lap_completed)
        self._bus.subscribe(LapsPurged, self._on_laps_purged)

    def dispose(self) -> None:
        """Cancela as inscrições no barramento."""
        self._bus.unsubscribe(LapCompleted, self._on_lap_completed)
        self._bus.unsubscribe(LapsPurged, self._on_laps_purged)

    @property
    def rows(self) -> list[LapRow]:
        return self._rows

    @property
    def track_id(self) -> int | None:
        return self._track_id

    def set_track(self, track_id: int | None) -> None:
        self._track_id = track_id
        self.refresh()

    def set_filter(self, text: str) -> None:
        """Filtra por nome do carro, id ou tempo. Aplicado sobre o que já está
        em memória — não vai ao banco a cada tecla digitada."""
        self._filter = (text or "").strip().lower()
        self.laps_changed.emit(self._apply_filter(self._rows))

    def refresh(self) -> None:
        # Sem pista selecionada, a lista mostra as voltas **sem pista** — as
        # que o reconhecimento automático não conseguiu identificar. Antes esta
        # tela ficava vazia, e uma volta salva sem pista simplesmente sumia.
        laps = self._laps.get_by_track(self._track_id)
        if not laps:
            self._rows = []
            self.ideal = IdealLap(total_ms=None)
            self.laps_changed.emit([])
            return

        lap_ids = [lap.id for lap in laps if lap.id is not None]

        # Duas consultas em lote no lugar de duas por volta. Com 50 voltas, a
        # versão antiga fazia 101 consultas para desenhar a tabela.
        sectors = self._laps.get_sector_times_batch(lap_ids)
        car_names = self._laps.car_names_batch(lap_ids)

        # O troféu vem do mesmo critério que o repositório usa para escolher a
        # referência do delta: volta completa, menor tempo, desempate pelo id
        # mais antigo. Comparar com o mínimo aqui daria troféu para as duas
        # voltas num empate, e podia marcar uma volta incompleta que nunca
        # seria a referência.
        best = self._laps.get_best(self._track_id)
        best_id = best.id if best else None

        self._rows = [
            LapRow(
                lap=lap,
                car_name=car_names.get(lap.id),
                sector_times=sectors.get(lap.id, []),
                is_best=(lap.id == best_id),
                is_complete=lap.is_complete,
                is_valid=lap.is_valid,
                has_notes=bool(lap.notes or lap.setup),
            )
            for lap in laps
        ]
        # Volta ideal da pista: o melhor de cada setor entre as voltas que
        # disputam recorde. Parciais e inválidas ficam de fora pelo mesmo
        # motivo que não viram recorde.
        self.ideal = ideal_lap({
            lap.id: sectors.get(lap.id, [])
            for lap in laps
            if lap.is_complete and lap.is_valid and lap.id is not None
        })
        self.laps_changed.emit(self._apply_filter(self._rows))

    def delete_lap(self, lap_id: int) -> None:
        try:
            self._laps.delete(lap_id)
            # Avisa o serviço de telemetria: se a volta apagada era a melhor,
            # o delta ao vivo estaria comparando contra algo que não existe
            # mais até a próxima troca de pista.
            self._bus.publish(LapDeleted(lap_id=lap_id, track_id=self._track_id))
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível excluir a volta: {exc}")

    def available_track_names(self) -> list[str]:
        """Pistas já conhecidas, para oferecer na hora de atribuir."""
        if self._tracks is None:
            return []
        return [t.name for t in self._tracks.get_all()]

    def assign_track_by_name(self, lap_id: int, name: str) -> bool:
        """Move a volta para a pista de nome informado, criando-a se preciso.

        Por nome e não por id porque é assim que o usuário pensa no problema —
        e porque a pista pode ainda não existir no banco, que é justamente o
        caso da primeira volta num circuito novo.
        """
        nome = (name or "").strip()
        if not nome:
            self.error.emit("Informe o nome da pista.")
            return False
        if self._tracks is None:
            self.error.emit("Catálogo de pistas indisponível.")
            return False
        try:
            track_id = self._tracks.get_or_create(nome)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível criar a pista: {exc}")
            return False
        return self.assign_track(lap_id, track_id)

    def assign_track(self, lap_id: int, track_id: int) -> bool:
        """Move uma volta sem pista para a pista informada.

        É a saída manual para quando o reconhecimento pelo traçado não decide —
        primeira volta num circuito novo, volta parcial, ou dois traçados
        parecidos demais. Sem isto, a volta ficaria salva e sem uso.
        """
        if track_id is None:
            self.error.emit("Escolha uma pista antes de atribuir.")
            return False
        try:
            self._laps.assign_track(lap_id, track_id)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível atribuir a pista: {exc}")
            return False
        self._learn_from(lap_id, track_id)
        # A volta saiu deste grupo; a lista precisa refletir isso na hora.
        self.refresh()
        self._bus.publish(LapCompleted(lap=Lap(id=lap_id, track_id=track_id),
                                       lap_id=lap_id, is_best=False))
        return True

    def _learn_from(self, lap_id: int, track_id: int) -> None:
        """Usa a volta recém-atribuída para ensinar o traçado da pista.

        É o caso mais valioso para aprender: o reconhecimento não soube
        decidir e uma pessoa respondeu. Sem isto, a próxima volta naquele
        circuito cairia de novo no grupo sem pista, e a de depois também.
        Só volta completa serve — meia volta descreveria meio circuito.
        """
        if self._identifier is None:
            return
        try:
            lap = self._laps.get_by_id(lap_id)
            if lap is not None and lap.is_complete:
                self._identifier.learn(track_id, lap.points)
        except Exception:  # noqa: BLE001
            # Aprender é conveniência; a atribuição já foi feita.
            pass

    def set_lap_valid(self, lap_id: int, is_valid: bool) -> None:
        """Marca a volta como válida ou inválida.

        Publica `LapDeleted` mesmo sem apagar nada: a volta pode ter sido o
        recorde, e a referência do delta precisa ser recarregada. O nome do
        evento fala de "sumiu da disputa", não de "sumiu do banco".
        """
        try:
            self._laps.set_valid(lap_id, is_valid)
            self._bus.publish(LapDeleted(lap_id=lap_id, track_id=self._track_id))
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível alterar a volta: {exc}")

    def export_lap(self, lap_id: int, destino) -> bool:
        """Grava a volta num arquivo avulso. Devolve se deu certo."""
        from src.infrastructure.storage.file_lap_storage import FileLapStorage

        lap = self._laps.get_by_id(lap_id)
        if lap is None:
            self.error.emit("Volta não encontrada.")
            return False
        try:
            FileLapStorage(self._export_scratch_dir()).export_lap(lap, destino)
            return True
        except OSError as exc:
            self.error.emit(f"Não foi possível exportar: {exc}")
            return False

    def export_lap_csv(self, lap_id: int, destino) -> bool:
        """Grava as amostras da volta em CSV, para abrir em planilha."""
        lap = self._laps.get_by_id(lap_id)
        if lap is None or not lap.points:
            self.error.emit("Esta volta não tem amostras para exportar.")
            return False
        try:
            Path(destino).write_text(lap_to_csv(lap.points), encoding="utf-8")
            return True
        except OSError as exc:
            self.error.emit(f"Não foi possível exportar: {exc}")
            return False

    # ---------- notas e acerto ----------

    def notes_for(self, lap_id: int) -> tuple[str, str]:
        """(anotações, acerto) da volta. Vazios se não houver."""
        linha = next((r for r in self._rows if r.lap.id == lap_id), None)
        if linha is None:
            return "", ""
        return linha.lap.notes, linha.lap.setup

    def set_notes(self, lap_id: int, notes: str, setup: str) -> bool:
        try:
            self._laps.set_notes(lap_id, notes.strip(), setup.strip())
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível salvar as anotações: {exc}")
            return False
        self.refresh()
        return True

    # ---------- setores da pista ----------

    def sector_fractions_text(self) -> str:
        """Limites de setor da pista aberta, em % da volta ("31, 68")."""
        if self._tracks is None or self._track_id is None:
            return ""
        track = self._tracks.get_by_id(self._track_id)
        if track is None or not track.sector_fractions:
            return ""
        # O último limite é sempre 100 % e não precisa ser digitado.
        return ", ".join(f"{f * 100:g}" for f in track.sector_fractions[:-1])

    def set_sector_fractions_from_text(self, texto: str) -> bool:
        """Define os setores a partir de percentuais separados por vírgula.

        Vazio volta à divisão em partes iguais. Os valores precisam ser
        crescentes e estar entre 0 e 100 — um limite fora de ordem produziria
        setor de tempo negativo.
        """
        if self._tracks is None or self._track_id is None:
            self.error.emit("Escolha uma pista antes de ajustar os setores.")
            return False

        bruto = (texto or "").replace(";", ",").strip()
        if not bruto:
            self._tracks.set_sector_fractions(self._track_id, None)
            return True
        try:
            valores = [float(v.strip().rstrip("%")) for v in bruto.split(",") if v.strip()]
        except ValueError:
            self.error.emit("Use números separados por vírgula, por exemplo: 31, 68")
            return False
        if (
            not valores
            or any(not 0 < v < 100 for v in valores)
            or any(b <= a for a, b in zip(valores, valores[1:]))
        ):
            self.error.emit(
                "Os limites precisam ser crescentes e ficar entre 0 e 100 (ex.: 31, 68)."
            )
            return False
        self._tracks.set_sector_fractions(
            self._track_id, [v / 100.0 for v in valores] + [1.0]
        )
        return True

    def import_lap(self, origem) -> bool:
        """Lê uma volta de arquivo e grava no repositório atual.

        O `id` do arquivo é descartado de propósito: ele veio de outro banco e
        colidiria com uma volta existente. A pista também é reatribuída para a
        pista aberta — importar uma volta de Interlagos enquanto se olha
        Suzuka misturaria os históricos.
        """
        from src.infrastructure.storage.file_lap_storage import (
            FileLapStorage,
            UnsupportedLapFile,
        )

        if self._track_id is None:
            self.error.emit("Escolha uma pista antes de importar.")
            return False
        try:
            lap = FileLapStorage.read_lap_file(origem)
        except UnsupportedLapFile as exc:
            self.error.emit(str(exc))
            return False

        if not lap.points:
            self.error.emit("O arquivo não contém amostras de telemetria.")
            return False

        lap.id = None
        lap.track_id = self._track_id
        try:
            self._laps.save(lap)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível importar: {exc}")
            return False
        self._bus.publish(LapCompleted(lap=lap, lap_id=lap.id or 0, is_best=False))
        self.refresh()
        return True

    @staticmethod
    def _export_scratch_dir():
        """Diretório efêmero exigido pelo construtor do FileLapStorage.

        A exportação escreve no destino escolhido pelo usuário, não aqui — mas
        a classe precisa de um diretório para existir.
        """
        import tempfile

        return tempfile.mkdtemp(prefix="hanna-export-")

    def clear_track(self) -> None:
        """Apaga todas as voltas da pista. A confirmação é da View — o
        ViewModel assume que a decisão já foi tomada."""
        if self._track_id is None:
            return
        try:
            self._laps.delete_by_track(self._track_id)
            self._bus.publish(LapDeleted(lap_id=-1, track_id=self._track_id))
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Não foi possível limpar os dados: {exc}")

    def _apply_filter(self, rows: list[LapRow]) -> list[LapRow]:
        if not self._filter:
            return rows
        needle = self._filter
        return [
            r
            for r in rows
            if needle in (r.car_name or "").lower()
            or needle in str(r.lap.id or "")
            or needle in self._format_time(r.lap.lap_time_ms)
        ]

    @staticmethod
    def _format_time(ms: int) -> str:
        if not ms or ms < 0:
            return ""
        total = ms / 1000
        return f"{int(total // 60)}:{total % 60:06.3f}"
