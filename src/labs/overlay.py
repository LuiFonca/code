"""
Overlay: uma janela mínima, sempre por cima, com o essencial.

Serve para segunda tela ou para ficar sobre a transmissão do jogo: velocidade,
marcha e os dois deltas, sem o resto do painel.

O ponto técnico é que ela não precisa de nada novo. O `LiveViewModel` já
separa a chegada dos dados da pintura, e aceita quantas telas quiserem
escutar — esta é só mais uma, ao lado da aba Ao Vivo.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


def _delta_text(valor) -> str:
    return "--" if valor is None else f"{valor:+.2f}"


class OverlayWindow(QWidget):
    """Janela compacta alimentada pelo mesmo ViewModel da aba Ao Vivo."""

    def __init__(self, view_model, parent=None):
        super().__init__(parent)
        self._vm = view_model
        self.setWindowTitle("HANNA — overlay")
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setStyleSheet(
            "background: #12141a; color: #ffffff; font-weight: 800;"
        )

        self.speed = QLabel("--")
        self.speed.setStyleSheet("font-size: 40px;")
        self.gear = QLabel("--")
        self.gear.setStyleSheet("font-size: 40px; color: #4f7cff;")
        self.delta_best = QLabel("--")
        self.delta_previous = QLabel("--")
        for rotulo in (self.delta_best, self.delta_previous):
            rotulo.setStyleSheet("font-size: 22px;")

        topo = QHBoxLayout()
        topo.addWidget(self.speed)
        topo.addWidget(self.gear)
        base = QHBoxLayout()
        base.addWidget(self.delta_best)
        base.addWidget(self.delta_previous)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.addLayout(topo)
        layout.addLayout(base)

        self._vm.frame_updated.connect(self._on_frame)
        self._vm.delta_updated.connect(self._on_delta)
        self._vm.stale_entered.connect(self._clear)

    def _on_frame(self, event) -> None:
        self.speed.setText(f"{event.point.speed_kmh:.0f}")
        self.gear.setText(str(event.point.gear))

    def _on_delta(self, best, previous) -> None:
        self.delta_best.setText(_delta_text(best))
        self.delta_previous.setText(_delta_text(previous))

    def _clear(self) -> None:
        # Travessão, não zero: sem sinal não é carro parado.
        for rotulo in (self.speed, self.gear, self.delta_best, self.delta_previous):
            rotulo.setText("--")
