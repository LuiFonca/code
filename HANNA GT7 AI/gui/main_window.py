"""
Janela principal do HANNA GT7 AI.
Barra de conexão fixa no topo (IP + pista), abas no meio
(Ao Vivo / Histórico / Comparação), rodapé de status fixo embaixo.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTabWidget, QComboBox
)
from PySide6.QtGui import QFont

from telemetry.listener_thread import TelemetryListenerThread
from analysis import lap_storage
from analysis.lap_recorder import LapRecorder
from gui.widgets import format_ms
from gui.tabs.live_tab import LiveDashboardTab
from gui.tabs.history_tab import HistoryTab
from gui.tabs.comparison_tab import ComparisonTab

# A telemetria chega a ~60 pacotes/segundo (necessário para a análise de
# desvios ser precisa), mas repintar a tela nessa frequência é desperdício
# na maioria dos casos. 40Hz aqui é um valor confortável: bem mais fluido
# que o mínimo perceptível, sem chegar nos 60Hz (que raramente trazem
# ganho visual perceptível e pesam mais em máquinas fracas). Os frames
# são guardados (só o mais recente importa para exibição) e um timer à
# parte decide quando repintar.
UI_REFRESH_HZ = 40
UI_REFRESH_INTERVAL_MS = int(1000 / UI_REFRESH_HZ)


DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #12141a;
    color: #e8e8ec;
    font-family: 'Segoe UI', Arial, sans-serif;
}
QLineEdit {
    background-color: #1c1f27;
    border: 1px solid #2a2e3a;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 14px;
    color: #e8e8ec;
}
QLineEdit:focus {
    border: 1px solid #4f7cff;
}
QComboBox {
    background-color: #1c1f27;
    border: 1px solid #2a2e3a;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    color: #e8e8ec;
    min-width: 160px;
}
QPushButton {
    background-color: #4f7cff;
    border: none;
    border-radius: 6px;
    padding: 9px 20px;
    font-size: 14px;
    font-weight: 600;
    color: white;
}
QPushButton:hover {
    background-color: #6690ff;
}
QPushButton:disabled {
    background-color: #2a2e3a;
    color: #6b6f7a;
}
QPushButton#stopButton {
    background-color: #2a2e3a;
}
QPushButton#stopButton:hover {
    background-color: #3a3f4d;
}
#statusPill {
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
}
#card {
    background-color: #1a1d25;
    border-radius: 12px;
    border: 1px solid #23262f;
}
#metricValue {
    font-size: 32px;
    font-weight: 700;
    color: #ffffff;
}
#metricLabel {
    font-size: 12px;
    color: #8a8e99;
    font-weight: 600;
    letter-spacing: 1px;
}
QProgressBar {
    background-color: #23262f;
    border-radius: 4px;
    height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    border-radius: 4px;
}
QTabWidget::pane {
    border: none;
}
QTabBar::tab {
    background-color: transparent;
    color: #8a8e99;
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 600;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #ffffff;
    border-bottom: 2px solid #4f7cff;
}
QTabBar::tab:hover {
    color: #e8e8ec;
}
"""

# IP padrão pré-preenchido no campo de conexão, para não precisar digitar
# toda vez. Pode ser alterado livremente na interface a qualquer momento.
DEFAULT_PS_IP = "192.168.15.156"

# Nome de pista usado quando o usuário não informa nenhum. O protocolo do
# GT7 não expõe o nome/id da pista atual, então essa identificação é
# sempre manual — o usuário digita ou escolhe o nome antes de conectar.
DEFAULT_TRACK_NAME = "Pista não identificada"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HANNA GT7 AI")
        self.resize(1280, 860)
        self.setMinimumSize(1024, 700)
        self.setStyleSheet(DARK_STYLE)

        lap_storage.init_db()

        self.listener_thread: TelemetryListenerThread | None = None
        self.lap_recorder: LapRecorder | None = None
        self._latest_frame = None  # sempre guarda só o frame mais recente
        self._latest_delta = None

        self._build_ui()

        # Timer de repintura da UI, independente da taxa de chegada de dados.
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(UI_REFRESH_INTERVAL_MS)
        self._ui_timer.timeout.connect(self._render_latest_frame)
        self._ui_timer.start()

    # ---------- construção da interface ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        root.addWidget(self._build_connection_bar())

        self.tabs = QTabWidget()
        self.live_tab = LiveDashboardTab()
        self.history_tab = HistoryTab(track_id=None)
        self.comparison_tab = ComparisonTab(track_id=None)

        self.tabs.addTab(self.live_tab, "Ao Vivo")
        self.tabs.addTab(self.history_tab, "Histórico")
        self.tabs.addTab(self.comparison_tab, "Comparação")
        root.addWidget(self.tabs, stretch=1)

        # rodapé de status/alertas
        self.log_label = QLabel("Aguardando conexão...")
        self.log_label.setStyleSheet("color: #6b6f7a; font-size: 12px;")
        root.addWidget(self.log_label)

    def _build_connection_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        title = QLabel("HANNA GT7 AI")
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        title.setFont(title_font)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP do PlayStation (ex: 192.168.1.50)")
        self.ip_input.setText(DEFAULT_PS_IP)
        self.ip_input.setFixedWidth(220)

        # Seletor de pista: o GT7 não informa qual pista está sendo usada
        # via telemetria, então quem diz é o usuário. Editável para digitar
        # um nome novo, e populado com pistas já usadas antes para reuso
        # rápido (evita criar "Interlagos" e "interlagos" como pistas
        # diferentes por engano de digitação).
        self.track_input = QComboBox()
        self.track_input.setEditable(True)
        self.track_input.setInsertPolicy(QComboBox.NoInsert)
        self.track_input.lineEdit().setPlaceholderText("Nome da pista (ex: Interlagos)")
        self._reload_track_list()

        self.connect_button = QPushButton("Conectar")
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.stop_button = QPushButton("Desconectar")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.stop_button.setEnabled(False)

        self.status_pill = QLabel("Desconectado")
        self.status_pill.setObjectName("statusPill")
        self._set_status_pill("desconectado")

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.track_input)
        layout.addWidget(self.ip_input)
        layout.addWidget(self.connect_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.status_pill)

        return frame

    def _reload_track_list(self):
        """Repopula o combo de pistas com as já usadas, preservando o
        texto digitado atualmente (se houver)."""
        current_text = self.track_input.currentText()
        self.track_input.blockSignals(True)
        self.track_input.clear()
        tracks = lap_storage.list_tracks()
        for track_id, name, lap_count in tracks:
            label = f"{name} ({lap_count} voltas)" if lap_count else name
            self.track_input.addItem(label, (track_id, name))
        self.track_input.setCurrentText(current_text)
        self.track_input.blockSignals(False)

    # ---------- lógica de conexão ----------

    def _set_status_pill(self, state: str):
        colors = {
            "desconectado": ("#2a2e3a", "#9a9ea8"),
            "conectando": ("#3a3410", "#f2c94c"),
            "recebendo": ("#123a1f", "#3ddc84"),
            "sem_sinal": ("#3a2410", "#f2994a"),
            "erro": ("#3a1414", "#ff5c5c"),
        }
        labels = {
            "desconectado": "Desconectado",
            "conectando": "Conectando...",
            "recebendo": "● Recebendo dados",
            "sem_sinal": "Sem sinal",
            "erro": "Erro",
        }
        bg, fg = colors.get(state, colors["desconectado"])
        self.status_pill.setText(labels.get(state, state))
        self.status_pill.setStyleSheet(
            f"#statusPill {{ background-color: {bg}; color: {fg}; }}"
        )

    def _resolve_track_name(self) -> str:
        """Pega o nome da pista digitado/selecionado, ignorando o sufixo
        '(N voltas)' que é só decorativo caso o usuário tenha escolhido
        um item existente da lista sem editar."""
        data = self.track_input.currentData()
        if data is not None:
            _, name = data
            return name
        text = self.track_input.currentText().strip()
        return text or DEFAULT_TRACK_NAME

    def _on_connect_clicked(self):
        ip = self.ip_input.text().strip()
        if not ip:
            self.log_label.setText("Digite o IP do PlayStation antes de conectar.")
            return

        track_name = self._resolve_track_name()
        track_id = lap_storage.get_or_create_track(track_name)

        if self.lap_recorder is None:
            self.lap_recorder = LapRecorder(track_id)
            self.lap_recorder.lap_saved.connect(self._on_lap_saved)
            self.lap_recorder.delta_changed.connect(self._on_delta_changed)
        else:
            self.lap_recorder.set_track(track_id)

        self.history_tab.set_track(track_id)
        self.comparison_tab.set_track(track_id)

        self.connect_button.setEnabled(False)
        self.ip_input.setEnabled(False)
        self.track_input.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.log_label.setText(f"Conectando em {ip} (pista: {track_name}) ...")

        self.listener_thread = TelemetryListenerThread(ip)
        self.listener_thread.frame_received.connect(self._on_frame)
        self.listener_thread.frame_received.connect(self.lap_recorder.on_frame)
        self.listener_thread.status_changed.connect(self._on_status)
        self.listener_thread.error_occurred.connect(self._on_error)
        self.listener_thread.start()

    def _on_stop_clicked(self):
        if self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None

        self.connect_button.setEnabled(True)
        self.ip_input.setEnabled(True)
        self.track_input.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_status_pill("desconectado")
        self.log_label.setText("Desconectado.")
        self._reload_track_list()

    # ---------- reação aos sinais da thread ----------

    def _on_status(self, state: str):
        self._set_status_pill(state)
        if state == "sem_sinal":
            self.log_label.setText(
                "Sem pacotes recebidos. Confira se o jogo está em uma sessão ativa (corrida/track day)."
            )
        elif state == "recebendo":
            self.log_label.setText("Recebendo telemetria normalmente.")

    def _on_error(self, message: str):
        self._set_status_pill("erro")
        self.log_label.setText(message)
        self.connect_button.setEnabled(True)
        self.ip_input.setEnabled(True)
        self.track_input.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _on_lap_saved(self, lap_id: int, lap_time_ms: int, is_best: bool):
        formatted = format_ms(lap_time_ms)
        if is_best:
            self.log_label.setText(f"🏁 Nova melhor volta salva: {formatted} (id {lap_id})")
        else:
            self.log_label.setText(f"🏁 Volta salva: {formatted} (id {lap_id})")

        # Mantém o histórico e a lista de voltas da aba de comparação
        # sempre atualizados, sem precisar clicar em nada manualmente.
        self.history_tab.refresh()
        self.comparison_tab.refresh_lap_list()

    def _on_frame(self, frame):
        # Chamado a ~60x/s pela thread de captura. Propositalmente NÃO toca
        # a UI aqui — só guarda o dado mais recente. Quem atualiza a tela é
        # o timer separado (_render_latest_frame), a uma taxa mais leve.
        self._latest_frame = frame

    def _on_delta_changed(self, delta_seconds):
        self._latest_delta = delta_seconds

    def _render_latest_frame(self):
        frame = self._latest_frame
        if frame is None:
            return
        self.live_tab.render_frame(frame)
        self.live_tab.render_delta(self._latest_delta)

    def closeEvent(self, event):
        if self.listener_thread:
            self.listener_thread.stop()
        event.accept()
