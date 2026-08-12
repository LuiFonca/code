"""
Widgets pequenos e reutilizáveis, usados em mais de uma aba da interface.
"""

from PySide6.QtWidgets import QVBoxLayout, QLabel, QFrame, QProgressBar
from PySide6.QtCore import Qt


class MetricCard(QFrame):
    """Card simples exibindo um rótulo e um valor grande (ex: velocidade, RPM)."""

    def __init__(self, label: str, unit: str = ""):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)

        self.label_widget = QLabel(label.upper())
        self.label_widget.setObjectName("metricLabel")

        self.value_widget = QLabel("--")
        self.value_widget.setObjectName("metricValue")

        self.unit = unit

        layout.addWidget(self.label_widget)
        layout.addWidget(self.value_widget)

    def set_value(self, value):
        self.value_widget.setText(f"{value}{self.unit}")


class BarCard(QFrame):
    """Card com barra de progresso (throttle / freio)."""

    def __init__(self, label: str, color: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)

        self.label_widget = QLabel(label.upper())
        self.label_widget.setObjectName("metricLabel")

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")

        layout.addWidget(self.label_widget)
        layout.addWidget(self.bar)

    def set_value(self, percent: float):
        self.bar.setValue(int(max(0, min(100, percent))))


class DeltaCard(QFrame):
    """Card grande mostrando o delta ao vivo contra uma volta de referência.
    Verde = mais rápido que a referência nesse ponto da pista;
    vermelho = mais devagar."""

    def __init__(self, label: str = "DELTA VS MELHOR VOLTA"):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setAlignment(Qt.AlignCenter)

        self.label_widget = QLabel(label)
        self.label_widget.setAlignment(Qt.AlignCenter)
        self.label_widget.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )

        self.value_widget = QLabel("--")
        self.value_widget.setAlignment(Qt.AlignCenter)
        self._set_neutral()

        layout.addWidget(self.label_widget)
        layout.addWidget(self.value_widget)

    def set_delta(self, delta_seconds):
        if delta_seconds is None:
            self.value_widget.setText("--")
            self._set_neutral()
            return

        sign = "+" if delta_seconds >= 0 else ""
        self.value_widget.setText(f"{sign}{delta_seconds:.2f}s")

        if delta_seconds > 0.02:
            self._set_color("#ff5c5c", "#3a1414")
        elif delta_seconds < -0.02:
            self._set_color("#3ddc84", "#0f2a18")
        else:
            self._set_neutral()

    def _set_color(self, fg: str, bg: str):
        self.value_widget.setStyleSheet(
            f"color: {fg}; font-size: 42px; font-weight: 800;"
        )
        self.setStyleSheet(
            f"QFrame#card {{ background-color: {bg}; border-radius: 12px; border: 1px solid #23262f; }}"
        )

    def _set_neutral(self):
        self.value_widget.setStyleSheet("color: #ffffff; font-size: 42px; font-weight: 800;")
        self.setStyleSheet(
            "QFrame#card { background-color: #1a1d25; border-radius: 12px; border: 1px solid #23262f; }"
        )


def format_ms(ms) -> str:
    """Formata milissegundos como tempo de volta legível (ex: 1:28.450)."""
    if ms is None or ms < 0:
        return "--:--.---"
    total_seconds = ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:06.3f}"
