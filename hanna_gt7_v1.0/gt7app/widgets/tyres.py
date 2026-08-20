"""
Temperatura dos quatro pneus, por faixa de cor.

Um número em graus não diz nada a quem pilota. 78 °C é frio, ideal ou quente?
Depende do composto, e ninguém consulta tabela no meio de uma volta. A cor
responde de relance, que é o único jeito de essa informação ser útil enquanto se
dirige — e o layout em cruz mostra **onde**, que é o que transforma o dado em
diagnóstico:

- **Dianteiros quentes, traseiros frios**: subesterço, ou peso demais na frente.
- **Um lado inteiro mais quente**: circuito com curvas predominantes num sentido,
  ou geometria descompensada.
- **Tudo azul depois de várias voltas**: o composto não é para este ritmo.

As faixas são do pneu de corrida típico do GT7. Não existe temperatura "certa"
universal, então elas são constantes nomeadas e num lugar só: quem correr com
outro composto muda quatro números aqui e não caça condicionais pelo código.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Palette, Theme

#: Limites das faixas, em °C.
COLD_BELOW_C = 70.0
"""Abaixo disto o pneu não gera aderência: borracha fria escorrega."""

IDEAL_BELOW_C = 95.0
"""Janela de trabalho. É onde o composto entrega o que promete."""

WARM_BELOW_C = 110.0
"""Aquecendo demais: ainda funciona, mas degrada mais rápido."""

#: Acima de `WARM_BELOW_C` é superaquecimento — perde aderência e desgasta.

WHEEL_LABELS = ("DE", "DD", "TE", "TD")
"""Dianteiro esquerdo/direito, traseiro esquerdo/direito."""


def temperature_color(celsius: float, palette: Palette) -> str:
    """Cor da faixa. Quatro degraus, sem gradiente.

    Gradiente contínuo seria mais bonito e pior: a pergunta é "estou na janela
    ou não", que é categórica. Uma cor que muda pouco a pouco obriga a comparar
    tons entre si para responder, e ninguém faz isso a 200 km/h.
    """
    if celsius < COLD_BELOW_C:
        return palette.accent
    if celsius < IDEAL_BELOW_C:
        return palette.green
    if celsius < WARM_BELOW_C:
        return palette.orange
    return palette.red


def temperature_label(celsius: float) -> str:
    if celsius < COLD_BELOW_C:
        return "frio"
    if celsius < IDEAL_BELOW_C:
        return "ideal"
    if celsius < WARM_BELOW_C:
        return "quente"
    return "superaquecido"


class TyreTemperatures(QWidget):
    """Os quatro pneus em cruz, coloridos pela faixa de temperatura."""

    def __init__(self, theme: Theme, *, height: int = 150) -> None:
        super().__init__()
        self._theme = theme
        self._temps: tuple[float, float, float, float] | None = None

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ---------- dados ----------

    def set_temperatures(
        self, fl: float, fr: float, rl: float, rr: float
    ) -> None:
        self._temps = (fl, fr, rl, rr)
        self.update()

    def clear(self) -> None:
        self._temps = None
        self.update()

    @property
    def temperatures(self) -> tuple[float, float, float, float] | None:
        return self._temps

    # ---------- pintura ----------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette

        if self._temps is None:
            painter.setPen(QPen(QColor(palette.text_muted), 1.0))
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter),
                "sem dados de pneus",
            )
            painter.end()
            return

        self._paint_wheels(painter, palette)
        painter.end()

    def _wheel_rects(self) -> list[QRectF]:
        """Os quatro retângulos, na posição física da roda.

        A ordem — DE, DD, TE, TD — segue a do protocolo, e a posição na tela
        segue a do carro visto de cima. Trocar as duas coisas de lugar aqui
        significaria o gráfico dizer que o pneu quente é o de trás quando é o da
        frente, que é o tipo de erro que ninguém desconfia olhando.
        """
        largura = self.width()
        altura = self.height()
        margem = 12.0
        vao = 14.0

        util_w = max(largura - 2 * margem - vao, 20.0)
        util_h = max(altura - 2 * margem - vao, 20.0)
        w = util_w / 2.0
        h = util_h / 2.0

        esquerda = margem
        direita = margem + w + vao
        cima = margem
        baixo = margem + h + vao

        return [
            QRectF(esquerda, cima, w, h),
            QRectF(direita, cima, w, h),
            QRectF(esquerda, baixo, w, h),
            QRectF(direita, baixo, w, h),
        ]

    def _paint_wheels(self, painter: QPainter, palette: Palette) -> None:
        assert self._temps is not None
        rects = self._wheel_rects()

        rotulo = QFont(painter.font())
        rotulo.setPointSizeF(8.5)
        valor = QFont(painter.font())
        valor.setPointSizeF(15.0)
        valor.setBold(True)

        for rect, temp, nome in zip(rects, self._temps, WHEEL_LABELS, strict=True):
            cor = QColor(temperature_color(temp, palette))

            fundo = QColor(cor)
            fundo.setAlpha(45)
            painter.setPen(QPen(cor, 1.6))
            painter.setBrush(fundo)
            painter.drawRoundedRect(rect, 8.0, 8.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            painter.setFont(rotulo)
            painter.setPen(QPen(QColor(palette.text_muted), 1.0))
            painter.drawText(
                QRectF(rect.left(), rect.top() + 4, rect.width(), 14),
                int(Qt.AlignmentFlag.AlignCenter),
                nome,
            )

            painter.setFont(valor)
            painter.setPen(QPen(cor, 1.0))
            painter.drawText(
                QRectF(rect.left(), rect.center().y() - 12, rect.width(), 24),
                int(Qt.AlignmentFlag.AlignCenter),
                f"{temp:.0f}°",
            )

            painter.setFont(rotulo)
            painter.setPen(QPen(QColor(palette.text_secondary), 1.0))
            painter.drawText(
                QRectF(rect.left(), rect.bottom() - 18, rect.width(), 14),
                int(Qt.AlignmentFlag.AlignCenter),
                temperature_label(temp),
            )
