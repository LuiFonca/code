"""
Janela principal.

Cuida só do chrome: barra de conexão, barra de pista/carro, abas e rodapé.

A versão antiga tinha 30 métodos e acumulava ciclo de vida da thread, watchdog
de telemetria, folha de estilo, montagem de UI e reação a eventos. Aqui a
thread é do `TelemetryService`, o watchdog é do `LiveViewModel` e o estilo está
em `styles.py`.

As abas chegam como um dicionário de fábricas: a janela não sabe quais abas
existem nem quais ViewModels elas usam — quem decide isso é o composition root.
"""

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application.events.event_bus import EventBus
from ..domain.config import AppConfig
from ..application.events.events import (
    CarDetected,
    ConnectionStateChanged,
    LapCompleted,
    LapDiscarded,
    LapSaveFailed,
    LapsPurged,
    TrackCandidatesDetected,
)
from ..application.services.session_manager import SessionManager
from ..application.services.telemetry_service import TelemetryService
from ..domain.interfaces.car_repository import CarRepository
from ..domain.interfaces.track_repository import TrackRepository
from ..domain.models.car import Car
from ..domain.models.track import Track
from .preferences_dialog import PreferencesDialog
from .styles import DARK_STYLE, DANGER, STATUS_COLORS, STATUS_LABELS, TEXT_MUTED
from .widgets.widgets import format_ms

DEFAULT_PS_IP = "192.168.15.156"

NO_TRACK_TEXT = "Nenhuma pista definida"


class MainWindow(QMainWindow):
    def __init__(
        self,
        telemetry_service: TelemetryService,
        session_manager: SessionManager,
        event_bus: EventBus,
        track_repository: TrackRepository,
        car_repository: CarRepository,
        track_catalog: TrackRepository,
        tab_factories: dict[str, Callable[[], QWidget]],
        on_track_changed: Callable[[int | None], None] | None = None,
        set_ps_ip: Callable[[str], None] | None = None,
        config: AppConfig | None = None,
        on_config_changed: Callable[[AppConfig], None] | None = None,
    ):
        super().__init__()
        self.setWindowTitle("HANNA GT7 AI")
        self.resize(1280, 860)
        self.setMinimumSize(1024, 700)
        self.setStyleSheet(DARK_STYLE)

        self._service = telemetry_service
        self._session = session_manager
        self._bus = event_bus
        self._tracks = track_repository
        self._cars = car_repository
        self._track_catalog = track_catalog
        self._on_track_changed = on_track_changed
        self._set_ps_ip = set_ps_ip
        self._config = config or AppConfig()
        self._on_config_changed = on_config_changed
        # Enquanto True, mensagens genéricas de estado não sobrescrevem o erro
        # mostrado ao usuário. Ver `_on_connection_changed`.
        self._error_sticky = False

        self._build_ui(tab_factories)
        self._subscribe()
        self._reload_track_list()
        self._reload_car_list()
        self._update_info_bar()

    # ---------- construção ----------

    def _build_ui(self, tab_factories: dict[str, Callable[[], QWidget]]):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(12)

        root.addWidget(self._build_connection_bar())
        root.addWidget(self._build_info_bar())

        self.tabs = QTabWidget()
        for title, factory in tab_factories.items():
            self.tabs.addTab(factory(), title)
        root.addWidget(self.tabs, stretch=1)

        self.log_label = QLabel("Aguardando conexão...")
        self.log_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        root.addWidget(self.log_label)

    def _build_connection_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        title = QLabel("HANNA GT7 AI")
        font = QFont()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP do PlayStation (ex: 192.168.1.50)")
        self.ip_input.setText(self._config.ps_ip)
        self.ip_input.setMinimumWidth(140)
        self.ip_input.setMaximumWidth(240)

        # Editável e alterável com a captura em andamento: trocar de pista sem
        # desconectar é caso real, e reiniciar a sessão só por isso seria hostil.
        self.track_input = QComboBox()
        self.track_input.setEditable(True)
        self.track_input.setInsertPolicy(QComboBox.NoInsert)
        self.track_input.lineEdit().setPlaceholderText("Nome da pista (ex: Interlagos)")
        self.track_input.activated.connect(lambda _i: self._on_track_selected())
        self.track_input.lineEdit().editingFinished.connect(self._on_track_selected)

        self.car_input = QComboBox()
        self.car_input.setEditable(True)
        self.car_input.setInsertPolicy(QComboBox.NoInsert)
        self.car_input.lineEdit().setPlaceholderText("Carro (opcional)")
        self.car_input.activated.connect(lambda _i: self._on_car_selected())
        self.car_input.lineEdit().editingFinished.connect(self._on_car_selected)

        self.player_mode_checkbox = QCheckBox("Replay / IA (não gravar)")
        self.player_mode_checkbox.setToolTip(
            "O GT7 não informa se o carro está sendo controlado por um jogador ou "
            "por replay/IA. Marque isto quando souber que é replay/IA: a telemetria "
            "continua visível Ao Vivo, mas a volta não é salva nem conta para recordes."
        )
        self.player_mode_checkbox.toggled.connect(self._on_player_mode_toggled)

        self.connect_button = QPushButton("Conectar")
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.stop_button = QPushButton("Desconectar")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.stop_button.setEnabled(False)

        self.prefs_button = QPushButton("⚙")
        self.prefs_button.setObjectName("stopButton")
        self.prefs_button.setToolTip("Preferências")
        self.prefs_button.setFixedWidth(38)
        self.prefs_button.clicked.connect(self._open_preferences)

        self.status_pill = QLabel()
        self.status_pill.setObjectName("statusPill")
        self._set_status_pill("desconectado")

        layout.addWidget(title)
        layout.addStretch()
        for w in (
            self.track_input, self.car_input, self.player_mode_checkbox,
            self.ip_input, self.connect_button, self.stop_button,
            self.prefs_button, self.status_pill,
        ):
            layout.addWidget(w)
        return frame

    def _open_preferences(self):
        """Abre as preferências e aplica o que for aplicável em tempo real.

        Nem tudo pode mudar com a captura em andamento: o intervalo do toque e
        os limites de retenção são lidos na construção da thread e do
        repositório. A tela avisa isso, e aqui só o que é seguro é propagado.
        """
        from ..domain.config import save_config

        dialog = PreferencesDialog(self._config, self)
        if dialog.exec() != PreferencesDialog.Accepted:
            return

        nova = dialog.result_config()
        try:
            save_config(nova)
        except OSError as exc:
            self.log_label.setText(f"⚠ Não foi possível salvar as preferências: {exc}")
            return

        self._config = nova
        if not self._service.is_running:
            self.ip_input.setText(nova.ps_ip)
        if self._on_config_changed is not None:
            self._on_config_changed(nova)

        self._reset_log_style()
        self.log_label.setText(
            "Preferências salvas. Ajustes de rede e de histórico valem a partir "
            "da próxima conexão."
        )

    def _build_info_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(16)

        self._info_track = QLabel()
        self._info_track.setStyleSheet("color: #4f7cff; font-size: 15px; font-weight: 700;")
        self._info_car = QLabel()
        self._info_car.setStyleSheet("color: #c8cad0; font-size: 15px; font-weight: 700;")

        layout.addWidget(self._info_track)
        layout.addWidget(self._info_car)
        layout.addStretch()
        return frame

    def _subscribe(self):
        self._bus.subscribe(ConnectionStateChanged, self._on_connection_changed)
        self._bus.subscribe(LapCompleted, self._on_lap_completed)
        self._bus.subscribe(LapDiscarded, self._on_lap_discarded)
        self._bus.subscribe(LapSaveFailed, self._on_lap_save_failed)
        self._bus.subscribe(CarDetected, self._on_car_detected)
        self._bus.subscribe(TrackCandidatesDetected, self._on_track_candidates)
        self._bus.subscribe(LapsPurged, self._on_laps_purged)

    # ---------- seletores ----------

    def _reload_track_list(self):
        """Pistas já usadas no topo, catálogo do jogo abaixo.

        As duas fontes num combo só, separadas por divisória: quem já rodou na
        pista a encontra de imediato, e quem está começando ainda tem as 105 do
        jogo à disposição sem digitar o nome exato.
        """
        current = self.track_input.currentText()
        self.track_input.blockSignals(True)
        self.track_input.clear()

        used_names = set()
        if hasattr(self._tracks, "list_with_lap_count"):
            for track_id, name, lap_count in self._tracks.list_with_lap_count():
                label = f"{name} ({lap_count} voltas)" if lap_count else name
                self.track_input.addItem(label, (track_id, name))
                used_names.add(name)
        else:
            for track in self._tracks.get_all():
                self.track_input.addItem(track.name, (track.id, track.name))
                used_names.add(track.name)

        catalog = self._track_catalog.get_all()
        if catalog and used_names:
            self.track_input.insertSeparator(self.track_input.count())
        for track in catalog:
            if track.name not in used_names:
                self.track_input.addItem(track.name, (None, track.name))

        self.track_input.setCurrentText(current)
        self.track_input.blockSignals(False)

    def _reload_car_list(self):
        current = self.car_input.currentText()
        self.car_input.blockSignals(True)
        self.car_input.clear()
        for car in self._cars.get_all():
            self.car_input.addItem(car.name, (car.id, car.name))
        self.car_input.setCurrentText(current)
        self.car_input.blockSignals(False)

    @staticmethod
    def _resolve_combo_name(combo: QComboBox) -> str | None:
        """Nome escolhido num combo editável, resolvendo o rótulo para o valor real.

        Ler `currentData()` primeiro **não** funciona aqui: num QComboBox editável
        com `NoInsert`, `setCurrentText()` só altera a caixa de texto e deixa o
        `currentIndex` onde estava. `currentData()` acaba devolvendo sempre o
        item 0 da lista, ignorando o que o usuário digitou ou selecionou.

        Com o catálogo de 105 pistas carregado, isso significa que qualquer
        pista digitada seria gravada como a primeira da lista em ordem
        alfabética. Aqui o texto é a fonte de verdade, e o `data` só é
        consultado para traduzir o rótulo (que traz o sufixo decorativo
        "(N voltas)") no nome real.
        """
        text = combo.currentText().strip()
        if not text:
            return None
        index = combo.findText(text)
        if index >= 0:
            data = combo.itemData(index)
            if data is not None:
                return data[1] or None
        return text

    def _resolve_track_name(self) -> str | None:
        """Sem nome não há pista-padrão: uma inventada automaticamente
        misturaria voltas de circuitos diferentes no mesmo histórico."""
        return self._resolve_combo_name(self.track_input)

    def _resolve_car_name(self) -> str:
        # Vazio é escolha válida para carro (vira "Desconhecido"), ao
        # contrário da pista.
        return self._resolve_combo_name(self.car_input) or ""

    def _update_info_bar(self):
        track = self._resolve_track_name()
        car = self._resolve_car_name()
        self._info_track.setText(f"Pista: {track}" if track else f"Pista: {NO_TRACK_TEXT}")
        self._info_car.setText(f"Carro: {car}" if car else "Carro: --")

    # ---------- ações ----------

    def _apply_track(self) -> int | None:
        name = self._resolve_track_name()
        track = None
        if name:
            track_id = self._tracks.get_or_create(name)
            track = Track(id=track_id, name=name)
        self._session.set_track(track)
        return track.id if track else None

    def _apply_car(self) -> int | None:
        name = self._resolve_car_name()
        car_id = self._cars.get_or_create(name)
        car = self._cars.get_by_id(car_id)
        self._session.set_car(car or Car(id=car_id, name=name))
        return car_id

    def _on_track_selected(self):
        track_id = self._apply_track()
        self._service.reload_reference()
        self._reload_track_list()
        self._update_info_bar()
        if self._on_track_changed:
            self._on_track_changed(track_id)

        if track_id is None:
            self.log_label.setText(
                f"{NO_TRACK_TEXT} — voltas não serão salvas até você definir uma pista."
            )
        else:
            self.log_label.setText(
                f"Pista definida: {self._resolve_track_name()}. Novas voltas serão salvas nela."
            )

    def _on_car_selected(self):
        self._apply_car()
        self._reload_car_list()
        self._update_info_bar()

    def _on_player_mode_toggled(self, is_replay: bool):
        self._session.set_player_mode(not is_replay)
        self.log_label.setText(
            "Modo replay/IA ativo: telemetria visível Ao Vivo, mas nenhuma volta será salva."
            if is_replay
            else "Modo jogador ativo: voltas voltam a ser salvas normalmente."
        )

    def _on_connect_clicked(self):
        ip = self.ip_input.text().strip()
        if not ip:
            self.log_label.setText("Digite o IP do PlayStation antes de conectar.")
            return

        track_id = self._apply_track()
        self._apply_car()
        if self._on_track_changed:
            self._on_track_changed(track_id)
        self._update_info_bar()

        if self._set_ps_ip:
            self._set_ps_ip(ip)

        self.connect_button.setEnabled(False)
        self.ip_input.setEnabled(False)
        self.stop_button.setEnabled(True)
        # Nova tentativa de conexão zera o erro anterior.
        self._error_sticky = False
        self._reset_log_style()

        if track_id is None:
            self.log_label.setText(
                f"Conectando em {ip} — {NO_TRACK_TEXT}: defina uma pista para começar "
                "a salvar voltas (pode ser feito a qualquer momento, sem reconectar)."
            )
        else:
            self.log_label.setText(
                f"Conectando em {ip} (pista: {self._resolve_track_name()})..."
            )

        self._service.start()

    def _on_stop_clicked(self):
        # Durante a reconexão o mesmo botão serve para desistir.
        if self._service.is_reconnecting:
            self._service.cancel_reconnect()
        self._service.stop()
        self.stop_button.setText("Desconectar")
        self.connect_button.setEnabled(True)
        self.ip_input.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_status_pill("desconectado")
        self._error_sticky = False
        self._reset_log_style()
        self.log_label.setText(
            "Desconectado. Todos os dados coletados permanecem disponíveis."
        )
        self._reload_track_list()
        self._reload_car_list()

    # ---------- reação a eventos ----------

    def _set_status_pill(self, state: str):
        bg, fg = STATUS_COLORS.get(state, STATUS_COLORS["desconectado"])
        self.status_pill.setText(STATUS_LABELS.get(state, state))
        self.status_pill.setStyleSheet(
            f"#statusPill {{ background-color: {bg}; color: {fg}; }}"
        )

    def _on_connection_changed(self, event: ConnectionStateChanged):
        # O rótulo do botão volta ao normal antes de qualquer descarte de
        # evento: sair da reconexão precisa restaurar "Desconectar" mesmo
        # quando o evento que sinaliza isso chega com a captura já parada.
        if self.stop_button.text() == "Cancelar" and event.state in (
            "recebendo", "desconectado", "erro",
        ):
            self.stop_button.setText("Desconectar")

        # Ao parar, a thread ainda pode ter um "sem_sinal" na fila, emitido
        # antes de perceber o pedido de encerramento. Entregue depois, ele
        # sobrescreveria a mensagem de desconexão com um alerta de uma captura
        # que já não existe.
        if not self._service.is_running and event.state in (
            "conectando", "recebendo", "sem_sinal",
        ):
            return

        self._set_status_pill(event.state)
        messages = {
            "sem_sinal": "Sem pacotes recebidos. Confira se o jogo está numa sessão ativa.",
            "recebendo": "Recebendo telemetria normalmente.",
        }

        if event.message:
            # Mensagem de erro é "pegajosa": um console inalcançável dispara o
            # erro de rota e, logo depois, o timeout genérico do socket. Sem
            # isto, o texto que diz o que fazer ("confira o IP") seria
            # imediatamente sobrescrito por "sem pacotes recebidos".
            self._error_sticky = True
            self.log_label.setText(event.message)
            self.log_label.setStyleSheet(
                f"color: {DANGER}; font-size: 12px; font-weight: 700;"
            )
        elif event.state == "recebendo":
            self._error_sticky = False
            self._reset_log_style()
            self.log_label.setText(messages["recebendo"])
        elif event.state == "conectando" and self._error_sticky:
            # O toque ao console voltou a passar: o alerta anterior era
            # transitório e não deve continuar na tela assustando o usuário.
            self._error_sticky = False
            self._reset_log_style()
            self.log_label.setText(
                "Contato com o PlayStation restabelecido. Aguardando telemetria — "
                "o GT7 só transmite dentro de uma sessão (corrida ou track day)."
            )
        elif event.state in messages and not self._error_sticky:
            self.log_label.setText(messages[event.state])

        if event.state == "reconectando":
            # A captura caiu sozinha: Conectar fica travado (o app já está
            # tentando) e Desconectar vira o modo de desistir.
            self.connect_button.setEnabled(False)
            self.ip_input.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.stop_button.setText("Cancelar")
            return

        if event.state == "erro":
            # Nem todo erro derruba a captura: falha de heartbeat (console
            # inalcançável) é recuperável e a thread continua tentando, então
            # os botões não podem voltar ao estado "desconectado" — isso
            # deixaria o usuário sem como parar uma captura ainda ativa.
            still_running = self._service.is_running
            self.connect_button.setEnabled(not still_running)
            self.ip_input.setEnabled(not still_running)
            self.stop_button.setEnabled(still_running)

    def _on_lap_completed(self, event: LapCompleted):
        formatted = format_ms(event.lap.lap_time_ms)
        prefix = "🏁 Nova melhor volta salva" if event.is_best else "🏁 Volta salva"
        self._reset_log_style()
        self.log_label.setText(f"{prefix}: {formatted} (id {event.lap_id})")

    def _on_laps_purged(self, event: LapsPurged):
        """Avisa quando a retenção descarta voltas antigas.

        Antes isso acontecia em silêncio: o usuário via o histórico encolher
        sem entender por quê."""
        plural = "s" if event.count > 1 else ""
        self._reset_log_style()
        self.log_label.setText(
            f"{event.count} volta{plural} antiga{plural} removida{plural} pelo limite "
            "de histórico da pista. Os melhores tempos são sempre preservados."
        )

    def _on_lap_discarded(self, event: LapDiscarded):
        self._reset_log_style()
        self.log_label.setText(
            f"Volta de {format_ms(event.lap_time_ms)} concluída, mas não salva "
            f"({event.reason})."
        )

    def _on_lap_save_failed(self, event: LapSaveFailed):
        self.log_label.setText(f"⚠ {event.message}")
        self.log_label.setStyleSheet(f"color: {DANGER}; font-size: 12px; font-weight: 700;")

    def _reset_log_style(self):
        self.log_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")

    def _on_car_detected(self, event: CarDetected):
        # Só preenche se o usuário não escolheu nada: sobrescrever uma escolha
        # manual seria pior que não detectar.
        if self.car_input.currentText().strip():
            return
        self.car_input.setCurrentText(event.car_name)
        self._on_car_selected()
        self.log_label.setText(f"Carro detectado automaticamente: {event.car_name}")

    def _on_track_candidates(self, event: TrackCandidatesDetected):
        if not event.names or self._resolve_track_name():
            return
        if len(event.names) == 1:
            self.track_input.setCurrentText(event.names[0])
            self._on_track_selected()
            self.log_label.setText(f"Pista detectada automaticamente: {event.names[0]}")
        else:
            # Vários candidatos é o caso comum — a distância não distingue
            # traçados de comprimento parecido. Sugerir e deixar o usuário decidir.
            self.log_label.setText(
                f"Pistas possíveis: {', '.join(event.names[:3])}. "
                "Selecione a correta no campo acima."
            )

    def closeEvent(self, event):
        self._service.stop()
        event.accept()
