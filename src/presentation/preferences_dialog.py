"""
Tela de preferências.

Expõe os valores que antes só existiam como constantes no código. Cada campo
carrega uma explicação do que muda ao alterá-lo — sem isso, "teto de força G"
não significa nada para quem só quer pilotar.

Mudanças de rede e de retenção só valem na próxima conexão ou gravação; a
tela diz isso em vez de fingir que tudo é imediato.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from ..domain.config import AppConfig


class PreferencesDialog(QDialog):
    """Edita um `AppConfig` e devolve o resultado por `result_config()`."""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferências")
        self.setMinimumWidth(520)
        self._original = config

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        root.addWidget(self._build_network_group(config))
        root.addWidget(self._build_history_group(config))
        root.addWidget(self._build_telemetry_group(config))

        aviso = QLabel(
            "Alterações de rede valem na próxima conexão. Limites de histórico "
            "e de setor valem para voltas gravadas a partir de agora."
        )
        aviso.setWordWrap(True)
        aviso.setStyleSheet("color: #8b93a7; font-size: 11px;")
        root.addWidget(aviso)

        botoes = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        botoes.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        botoes.button(QDialogButtonBox.Ok).setText("Salvar")
        botoes.button(QDialogButtonBox.Cancel).setText("Cancelar")
        botoes.button(QDialogButtonBox.RestoreDefaults).setText("Restaurar padrões")
        root.addWidget(botoes)

    # ---------- grupos ----------

    def _build_network_group(self, config: AppConfig) -> QGroupBox:
        box = QGroupBox("Conexão")
        form = QFormLayout(box)

        self._ip = QLineEdit(config.ps_ip)
        self._ip.setPlaceholderText("192.168.1.50")
        form.addRow("IP do PlayStation", self._ip)

        self._heartbeat = QDoubleSpinBox()
        self._heartbeat.setRange(0.5, 120.0)
        self._heartbeat.setSuffix(" s")
        self._heartbeat.setValue(config.heartbeat_interval_s)
        self._heartbeat.setToolTip(
            "O console para de transmitir se não receber um toque periódico. "
            "Diminuir aumenta o tráfego; aumentar arrisca o console parar."
        )
        form.addRow("Intervalo do toque", self._heartbeat)

        self._auto_reconnect = QCheckBox("Reconectar automaticamente ao perder o sinal")
        self._auto_reconnect.setChecked(config.auto_reconnect)
        form.addRow("", self._auto_reconnect)

        self._stale = QDoubleSpinBox()
        self._stale.setRange(0.1, 60.0)
        self._stale.setSuffix(" s")
        self._stale.setSingleStep(0.5)
        self._stale.setValue(config.stale_timeout_s)
        self._stale.setToolTip(
            "Silêncio a partir do qual o painel vai para o estado neutro, para "
            "não confundir carro parado com transmissão perdida."
        )
        form.addRow("Silêncio até zerar o painel", self._stale)
        return box

    def _build_history_group(self, config: AppConfig) -> QGroupBox:
        box = QGroupBox("Histórico")
        form = QFormLayout(box)

        self._keep_best = QSpinBox()
        self._keep_best.setRange(1, 1000)
        self._keep_best.setValue(config.keep_best_per_track)
        self._keep_best.setToolTip("Recordes preservados por pista, sempre.")
        form.addRow("Melhores voltas por pista", self._keep_best)

        self._keep_recent = QSpinBox()
        self._keep_recent.setRange(1, 10000)
        self._keep_recent.setValue(config.keep_recent_per_track)
        self._keep_recent.setToolTip(
            "Voltas recentes preservadas por pista. Quem treina a mesma pista "
            "todo dia estoura 50 rápido."
        )
        form.addRow("Voltas recentes por pista", self._keep_recent)

        self._sectors = QSpinBox()
        self._sectors.setRange(1, 10)
        self._sectors.setValue(config.num_sectors)
        self._sectors.setToolTip(
            "O GT7 não transmite os pontos oficiais de setor; a volta é dividida "
            "em partes iguais, salvo ajuste específico da pista."
        )
        form.addRow("Setores por volta", self._sectors)
        return box

    def _build_telemetry_group(self, config: AppConfig) -> QGroupBox:
        box = QGroupBox("Telemetria e gráficos")
        form = QFormLayout(box)

        self._max_g = QDoubleSpinBox()
        self._max_g.setRange(0.5, 50.0)
        self._max_g.setSuffix(" g")
        self._max_g.setSingleStep(0.5)
        self._max_g.setValue(config.max_g)
        self._max_g.setToolTip(
            "Teto de sanidade. Carro real não passa de ~2 g; valores acima disso "
            "vêm de pacote perdido e estragariam a escala do gráfico."
        )
        form.addRow("Teto de força G", self._max_g)

        self._slip = QDoubleSpinBox()
        self._slip.setRange(0.01, 10.0)
        self._slip.setSingleStep(0.1)
        self._slip.setDecimals(2)
        self._slip.setValue(config.slip_saturation)
        self._slip.setToolTip(
            "Valor bruto de deslizamento que corresponde a 100 % no índice. "
            "Baixar torna o indicador mais sensível."
        )
        form.addRow("Saturação do índice de deslizamento", self._slip)

        self._points = QSpinBox()
        self._points.setRange(100, 100000)
        self._points.setSingleStep(500)
        self._points.setValue(config.max_plot_points)
        self._points.setToolTip(
            "Acima disto não há pixel na tela para distinguir os pontos, e o "
            "gráfico só fica mais lento."
        )
        form.addRow("Máximo de pontos por gráfico", self._points)
        return box

    # ---------- ações ----------

    def _restore_defaults(self):
        padrao = AppConfig()
        self._ip.setText(padrao.ps_ip)
        self._heartbeat.setValue(padrao.heartbeat_interval_s)
        self._auto_reconnect.setChecked(padrao.auto_reconnect)
        self._stale.setValue(padrao.stale_timeout_s)
        self._keep_best.setValue(padrao.keep_best_per_track)
        self._keep_recent.setValue(padrao.keep_recent_per_track)
        self._sectors.setValue(padrao.num_sectors)
        self._max_g.setValue(padrao.max_g)
        self._slip.setValue(padrao.slip_saturation)
        self._points.setValue(padrao.max_plot_points)

    def result_config(self) -> AppConfig:
        """Config montada a partir dos campos.

        Campos fora de faixa não chegam aqui — os próprios widgets limitam a
        entrada. O IP vazio cai no valor anterior em vez de virar string vazia,
        que produziria uma tentativa de conexão sem destino.
        """
        return AppConfig(
            ps_ip=self._ip.text().strip() or self._original.ps_ip,
            heartbeat_interval_s=self._heartbeat.value(),
            keep_best_per_track=self._keep_best.value(),
            keep_recent_per_track=self._keep_recent.value(),
            num_sectors=self._sectors.value(),
            max_g=self._max_g.value(),
            stale_timeout_s=self._stale.value(),
            slip_saturation=self._slip.value(),
            max_plot_points=self._points.value(),
            auto_reconnect=self._auto_reconnect.isChecked(),
            reconnect_initial_delay_s=self._original.reconnect_initial_delay_s,
            reconnect_max_delay_s=self._original.reconnect_max_delay_s,
        )
