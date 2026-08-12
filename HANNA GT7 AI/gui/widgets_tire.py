"""
Painel de temperatura dos 4 pneus organizado como a planta do carro
(dianteiros em cima, traseiros embaixo — cada um na posição correspondente
ao lado real), com cor indicando a faixa de temperatura e um pulso visual
quando o pneu está superaquecendo.

As faixas de temperatura abaixo são uma referência geral de pneus de
competição (frio / janela ideal / quente / superaquecendo), não um valor
oficial documentado pela Polyphony Digital — o próprio GT7 não expõe uma
"temperatura ideal" via telemetria, então isso é uma orientação visual,
não um dado exato do jogo.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QLabel, QFrame

COLD = QColor("#4f9fff")       # abaixo da janela de trabalho
OPTIMAL = QColor("#3ddc84")    # janela ideal
HOT = QColor("#f2994a")        # quente, ainda ok
OVERHEATING = QColor("#ff5c5c")  # superaquecendo — pisca

COLD_MAX = 70
OPTIMAL_MAX = 100
HOT_MAX = 120


def _band_for(celsius: float):
    if celsius < COLD_MAX:
        return COLD, False
    if celsius < OPTIMAL_MAX:
        return OPTIMAL, False
    if celsius < HOT_MAX:
        return HOT, False
    return OVERHEATING, True


class TireTempWidget(QFrame):
    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("tireCard")
        self.setFixedSize(110, 90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self.label_widget = QLabel(label)
        self.label_widget.setStyleSheet("color: #8a8e99; font-size: 10px; font-weight: 700; letter-spacing: 1px;")

        self.value_widget = QLabel("--°C")
        self.value_widget.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: 700;")

        layout.addWidget(self.label_widget)
        layout.addWidget(self.value_widget)
        layout.addStretch()

        self._is_hot = False
        self._pulse_on = False
        self._base_color = QColor("#2a2e3a")

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(450)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._apply_style(self._base_color)

    def set_temp(self, celsius: float):
        self.value_widget.setText(f"{celsius:.0f}°C")
        color, is_hot = _band_for(celsius)
        self._base_color = color

        if is_hot and not self._pulse_timer.isActive():
            self._pulse_timer.start()
        elif not is_hot and self._pulse_timer.isActive():
            self._pulse_timer.stop()

        self._is_hot = is_hot
        if not is_hot:
            self._apply_style(color)

    def _toggle_pulse(self):
        self._pulse_on = not self._pulse_on
        color = QColor("#ff2f2f") if self._pulse_on else self._base_color
        self._apply_style(color)

    def _apply_style(self, color: QColor):
        self.setStyleSheet(f"""
            QFrame#tireCard {{
                background-color: #1a1d25;
                border: 2px solid {color.name()};
                border-radius: 10px;
            }}
        """)


class TireTempPanel(QWidget):
    """Layout 2x2 imitando a planta do carro: FL/FR em cima, RL/RR embaixo."""

    def __init__(self):
        super().__init__()
        grid = QGridLayout(self)
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)

        self.fl = TireTempWidget("DIANT. ESQ.")
        self.fr = TireTempWidget("DIANT. DIR.")
        self.rl = TireTempWidget("TRAS. ESQ.")
        self.rr = TireTempWidget("TRAS. DIR.")

        grid.addWidget(self.fl, 0, 0)
        grid.addWidget(self.fr, 0, 2)
        grid.addWidget(self.rl, 1, 0)
        grid.addWidget(self.rr, 1, 2)

    def set_temps(self, fl: float, fr: float, rl: float, rr: float):
        self.fl.set_temp(fl)
        self.fr.set_temp(fr)
        self.rl.set_temp(rl)
        self.rr.set_temp(rr)
