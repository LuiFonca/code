"""
Janela principal do HANNA GT7 AI.
Barra de conexão fixa no topo (IP + pista + carro), abas no meio
(Ao Vivo / Histórico / Comparação), rodapé de status fixo embaixo.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTabWidget, QComboBox, QCheckBox
)
from PySide6.QtGui import QFont

from telemetry.listener_thread import TelemetryListenerThread
from analysis import lap_storage
from analysis.lap_recorder import LapRecorder
from gui.widgets import format_ms
from gui.tabs.live_tab import LiveDashboardTab
from gui.tabs.history_tab import HistoryTab
from gui.tabs.telemetry_tab import TelemetryTab

# A telemetria chega a ~60 pacotes/segundo (necessário para a análise de
# desvios ser precisa), mas repintar a tela nessa frequência é desperdício
# na maioria dos casos. 40Hz aqui é um valor confortável: bem mais fluido
# que o mínimo perceptível, sem chegar nos 60Hz (que raramente trazem
# ganho visual perceptível e pesam mais em máquinas fracas). Os frames
# são guardados (só o mais recente importa para exibição) e um timer à
# parte decide quando repintar.
UI_REFRESH_HZ = 40
UI_REFRESH_INTERVAL_MS = int(1000 / UI_REFRESH_HZ)

# Watchdog de telemetria (item 9): se nenhum frame novo chegar por mais que
# isto, a UI é considerada "sem dados" e passa para valores neutros — em vez
# de continuar mostrando o último valor recebido indefinidamente, o que
# confundiria "carro parado" (valor real 0) com "transmissão perdida"
# (ausência de dado). A ~60 pacotes/s, 1s de silêncio já é uma parada clara.
STALE_TIMEOUT_S = 1.0
WATCHDOG_INTERVAL_MS = 250


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
    font-size: 28px;
    font-weight: 700;
    color: #ffffff;
}
#metricLabel {
    font-size: 13px;
    color: #c8cad0;
    font-weight: 700;
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
    color: #b0b3bc;
    padding: 10px 18px;
    font-size: 14px;
    font-weight: 700;
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

# O protocolo do GT7 não expõe o nome/id da pista atual nem o modelo do
# carro nesta implementação, então essa identificação é sempre manual — o
# usuário digita ou escolhe antes/durante a conexão. Diferente do carro
# (que pode ficar como "Desconhecido" e ainda assim salvar normalmente),
# uma pista não escolhida NÃO gera um nome-padrão automático: sem pista
# válida, a sessão fica só ao vivo e nada é gravado permanentemente (ver
# LapRecorder.can_persist / item 7 do pedido de evolução).
NO_TRACK_TEXT = "Nenhuma pista definida"


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
        self._latest_delta_prev = None
        self._last_frame_monotonic = None  # relógio monotônico do último frame recebido
        self._is_stale = False

        self._build_ui()

        # Timer de repintura da UI, independente da taxa de chegada de dados.
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(UI_REFRESH_INTERVAL_MS)
        self._ui_timer.timeout.connect(self._render_latest_frame)
        self._ui_timer.start()

        # Watchdog de telemetria: roda o tempo todo (barato — só compara um
        # timestamp), mas só age quando existe uma conexão ativa que já
        # recebeu ao menos um frame (ver _check_stale).
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog_timer.timeout.connect(self._check_stale)
        self._watchdog_timer.start()

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
        self.telemetry_tab = TelemetryTab(track_id=None)

        self.tabs.addTab(self.live_tab, "Ao Vivo")
        self.tabs.addTab(self.history_tab, "Histórico")
        self.tabs.addTab(self.telemetry_tab, "Telemetria")
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
        self.ip_input.setMinimumWidth(140)
        self.ip_input.setMaximumWidth(240)

        # Seletor de pista: o GT7 não informa qual pista está sendo usada
        # via telemetria, então quem diz é o usuário. Editável para digitar
        # um nome novo, e populado com pistas já usadas antes para reuso
        # rápido (evita criar "Interlagos" e "interlagos" como pistas
        # diferentes por engano de digitação). Fica editável mesmo com o
        # PS5 já conectado: trocar a pista aqui NÃO exige desconectar (a
        # troca é propagada na hora para o LapRecorder — ver _on_track_changed).
        self.track_input = QComboBox()
        self.track_input.setEditable(True)
        self.track_input.setInsertPolicy(QComboBox.NoInsert)
        self.track_input.lineEdit().setPlaceholderText("Nome da pista (ex: Interlagos)")
        self.track_input.activated.connect(lambda _index: self._on_track_changed())
        self.track_input.lineEdit().editingFinished.connect(self._on_track_changed)
        self._reload_track_list()

        # Seletor de carro: mesma ideia da pista, mas o GT7 sem um ID de
        # carro validado nesta implementação faz do nome sempre uma escolha
        # manual. Diferente da pista, ficar em branco aqui NÃO bloqueia o
        # registro — vira "Desconhecido" (ver lap_storage.UNKNOWN_CAR_NAME).
        self.car_input = QComboBox()
        self.car_input.setEditable(True)
        self.car_input.setInsertPolicy(QComboBox.NoInsert)
        self.car_input.lineEdit().setPlaceholderText("Carro (opcional, ex: Porsche 911 GT3)")
        self.car_input.activated.connect(lambda _index: self._on_car_changed())
        self.car_input.lineEdit().editingFinished.connect(self._on_car_changed)
        self._reload_car_list()

        # Modo replay/IA: o GT7 não expõe nenhum flag confiável para
        # distinguir automaticamente jogador de IA/replay nesta
        # implementação (limitação do protocolo, não do app — ver README).
        # Então quem sabe que está vendo um replay/IA marca aqui: a
        # telemetria continua aparecendo Ao Vivo, mas nada é salvo como
        # volta válida (histórico/recordes/setores/ranking ficam intactos).
        self.player_mode_checkbox = QCheckBox("Replay / IA (não gravar)")
        self.player_mode_checkbox.setToolTip(
            "O GT7 não informa se o carro está sendo controlado por um jogador ou por "
            "replay/IA. Marque isto quando souber que é replay/IA: a telemetria continua "
            "visível Ao Vivo, mas a volta não é salva no histórico nem conta para recordes."
        )
        self.player_mode_checkbox.toggled.connect(self._on_player_mode_toggled)

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
        layout.addWidget(self.car_input)
        layout.addWidget(self.player_mode_checkbox)
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

    def _reload_car_list(self):
        """Repopula o combo de carros com os já usados, preservando o
        texto digitado atualmente (se houver)."""
        current_text = self.car_input.currentText()
        self.car_input.blockSignals(True)
        self.car_input.clear()
        for car_id, name in lap_storage.list_cars():
            self.car_input.addItem(name, (car_id, name))
        self.car_input.setCurrentText(current_text)
        self.car_input.blockSignals(False)

    # ---------- lógica de conexão ----------

    def _set_status_pill(self, state: str):
        colors = {
            "desconectado": ("#2a2e3a", "#9a9ea8"),
            "conectando": ("#3a3410", "#f2c94c"),
            "recebendo": ("#123a1f", "#3ddc84"),
            "stale": ("#2a2e3a", "#f2994a"),
            "sem_sinal": ("#3a2410", "#f2994a"),
            "erro": ("#3a1414", "#ff5c5c"),
        }
        labels = {
            "desconectado": "Desconectado",
            "conectando": "Conectando...",
            "recebendo": "● Conectado",
            "stale": "○ Sem dados",
            "sem_sinal": "Sem sinal",
            "erro": "Erro",
        }
        bg, fg = colors.get(state, colors["desconectado"])
        self.status_pill.setText(labels.get(state, state))
        self.status_pill.setStyleSheet(
            f"#statusPill {{ background-color: {bg}; color: {fg}; }}"
        )

    def _resolve_track_name(self) -> str | None:
        """Pega o nome da pista digitado/selecionado, ignorando o sufixo
        '(N voltas)' que é só decorativo caso o usuário tenha escolhido
        um item existente da lista sem editar. Retorna None se nada foi
        informado — nesse caso NÃO existe pista-padrão automática (ver
        NO_TRACK_TEXT): sem escolha explícita do usuário, não há histórico."""
        data = self.track_input.currentData()
        if data is not None:
            _, name = data
            return name or None
        text = self.track_input.currentText().strip()
        return text or None

    def _resolve_car_name(self) -> str:
        """Pega o nome do carro digitado/selecionado. Vazio é uma escolha
        válida aqui (vira 'Desconhecido' — ver lap_storage.UNKNOWN_CAR_NAME),
        diferente da pista."""
        data = self.car_input.currentData()
        if data is not None:
            _, name = data
            return name
        return self.car_input.currentText().strip()

    def _current_track_id(self):
        """None quando nenhuma pista válida foi escolhida (ver _resolve_track_name)."""
        name = self._resolve_track_name()
        return lap_storage.get_or_create_track(name) if name else None

    def _current_car_id(self):
        return lap_storage.get_or_create_car(self._resolve_car_name())

    def _on_connect_clicked(self):
        ip = self.ip_input.text().strip()
        if not ip:
            self.log_label.setText("Digite o IP do PlayStation antes de conectar.")
            return

        track_id = self._current_track_id()
        car_id = self._current_car_id()

        if self.lap_recorder is None:
            self.lap_recorder = LapRecorder(track_id, car_id)
            self.lap_recorder.lap_saved.connect(self._on_lap_saved)
            self.lap_recorder.lap_discarded.connect(self._on_lap_discarded)
            self.lap_recorder.delta_changed.connect(self._on_delta_changed)
            self.lap_recorder.delta_previous_changed.connect(self._on_delta_previous_changed)
            self.lap_recorder.car_detected.connect(self._on_car_detected)
            self.lap_recorder.track_candidates_detected.connect(self._on_track_candidates)
        else:
            self.lap_recorder.set_track(track_id)
            self.lap_recorder.set_car(car_id)

        self.history_tab.set_track(track_id)
        self.telemetry_tab.set_track(track_id)

        self.connect_button.setEnabled(False)
        self.ip_input.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._last_frame_monotonic = None
        self._is_stale = False

        if track_id is None:
            self.log_label.setText(
                f"Conectando em {ip} — {NO_TRACK_TEXT}: defina uma pista para começar a salvar voltas "
                "(pode ser feito a qualquer momento, sem reconectar)."
            )
        else:
            self.log_label.setText(f"Conectando em {ip} (pista: {self._resolve_track_name()}) ...")

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
        self.stop_button.setEnabled(False)
        self._last_frame_monotonic = None
        self._is_stale = False
        self._latest_frame = None
        self._set_status_pill("desconectado")
        self.log_label.setText("Desconectado. Todos os dados coletados permanecem disponíveis.")
        self._reload_track_list()
        self._reload_car_list()
        self.history_tab.refresh()
        self.telemetry_tab.refresh_lap_list()

    # ---------- troca de pista/carro/modo em tempo real (item 5) ----------
    # Todos estes handlers funcionam com o PS5 conectado ou não: se houver
    # uma conexão ativa, a troca é propagada na hora para o LapRecorder
    # (sem reiniciar/desconectar); se não houver, só fica pronta para
    # quando o usuário clicar em Conectar.

    def _on_track_changed(self):
        track_id = self._current_track_id()
        if self.lap_recorder is not None:
            self.lap_recorder.set_track(track_id)
        self.history_tab.set_track(track_id)
        self.telemetry_tab.set_track(track_id)
        self._reload_track_list()

        if track_id is None:
            self.log_label.setText(f"{NO_TRACK_TEXT} — voltas não serão salvas até você definir uma pista.")
        else:
            self.log_label.setText(f"Pista definida: {self._resolve_track_name()}. Novas voltas serão salvas nela.")

    def _on_car_changed(self):
        car_id = self._current_car_id()
        if self.lap_recorder is not None:
            self.lap_recorder.set_car(car_id)
        self._reload_car_list()

    def _on_player_mode_toggled(self, is_replay_checked: bool):
        if self.lap_recorder is not None:
            self.lap_recorder.set_player_mode(not is_replay_checked)
        if is_replay_checked:
            self.log_label.setText(
                "Modo replay/IA ativo: a telemetria continua visível Ao Vivo, mas nenhuma volta será salva."
            )
        else:
            self.log_label.setText("Modo jogador ativo: voltas voltam a ser salvas normalmente.")

    # ---------- reação aos sinais da thread ----------

    def _on_status(self, state: str):
        # "sem_sinal" vem do socket (3s sem NENHUM pacote, nem inválido) —
        # mais grosseiro que o watchdog de frames válidos (_check_stale,
        # 1s). Não sobrescreve o pill "stale" já mostrado pelo watchdog
        # nesse meio-tempo, mas atualiza a mensagem.
        if state == "sem_sinal":
            if not self._is_stale:
                self._set_status_pill("sem_sinal")
            self.log_label.setText(
                "Sem pacotes recebidos. Confira se o jogo está em uma sessão ativa (corrida/track day)."
            )
        elif state == "recebendo":
            if not self._is_stale:
                self._set_status_pill("recebendo")
            self.log_label.setText("Recebendo telemetria normalmente.")
        else:
            self._set_status_pill(state)

    def _on_error(self, message: str):
        self._set_status_pill("erro")
        self.log_label.setText(message)
        self.connect_button.setEnabled(True)
        self.ip_input.setEnabled(True)
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
        self.telemetry_tab.refresh_lap_list()

    def _on_lap_discarded(self, lap_time_ms: int):
        formatted = format_ms(lap_time_ms)
        reason = NO_TRACK_TEXT if self.lap_recorder and self.lap_recorder.track_id is None else "modo replay/IA"
        self.log_label.setText(f"Volta de {formatted} concluída, mas não salva ({reason}).")

    def _on_frame(self, frame):
        # Chamado a ~60x/s pela thread de captura. Propositalmente NÃO toca
        # a UI aqui — só guarda o dado mais recente. Quem atualiza a tela é
        # o timer separado (_render_latest_frame), a uma taxa mais leve.
        self._latest_frame = frame
        self._last_frame_monotonic = time.monotonic()
        if self._is_stale:
            # Dados voltaram a chegar: sai do estado "stale" imediatamente.
            # Os valores reais retomam sozinhos no próximo _render_latest_frame.
            self._is_stale = False
            self._set_status_pill("recebendo")
            self.log_label.setText("Telemetria voltou a chegar normalmente.")

    def _on_delta_changed(self, delta_seconds):
        self._latest_delta = delta_seconds

    def _on_delta_previous_changed(self, delta_seconds):
        self._latest_delta_prev = delta_seconds

    def _on_car_detected(self, car_name: str):
        if not car_name:
            return
        current = self.car_input.currentText().strip()
        if current:
            return
        self.car_input.setCurrentText(car_name)
        self._on_car_changed()
        self.log_label.setText(f"Carro detectado automaticamente: {car_name}")

    def _on_track_candidates(self, names: list):
        if not names:
            return
        current = self._resolve_track_name()
        if current:
            return
        if len(names) == 1:
            self.track_input.setCurrentText(names[0])
            self._on_track_changed()
            self.log_label.setText(f"Pista detectada automaticamente: {names[0]}")
        else:
            suggestion = ", ".join(names[:3])
            self.log_label.setText(f"Pistas possíveis detectadas: {suggestion}. Selecione a correta no campo acima.")

    def _check_stale(self):
        """Watchdog do item 9: sem um listener ativo que já tenha recebido
        ao menos um frame, não há o que verificar. Uma vez sem novo frame
        por mais que STALE_TIMEOUT_S, força a UI para o estado neutro em
        vez de deixar o último valor recebido congelado na tela."""
        if self.listener_thread is None or self._last_frame_monotonic is None:
            return
        if self._is_stale:
            return
        if time.monotonic() - self._last_frame_monotonic > STALE_TIMEOUT_S:
            self._is_stale = True
            self._set_status_pill("stale")
            self.log_label.setText("Sem dados de telemetria — verifique a conexão com o PS5.")
            self.live_tab.render_stale()
            self.live_tab.render_delta(None)
            self.live_tab.render_delta_previous(None)

    def _render_latest_frame(self):
        if self._is_stale:
            # O watchdog já pintou os valores neutros; não reaplica o
            # último frame conhecido por cima (isso re-introduziria o bug
            # de "valor congelado" que o watchdog existe para evitar).
            return
        frame = self._latest_frame
        if frame is None:
            return
        self.live_tab.render_frame(frame)
        self.live_tab.render_delta(self._latest_delta)
        self.live_tab.render_delta_previous(self._latest_delta_prev)

    def closeEvent(self, event):
        if self.listener_thread:
            self.listener_thread.stop()
        event.accept()
