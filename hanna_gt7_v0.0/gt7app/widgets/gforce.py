"""
Diagrama G-G — o círculo de atrito.

Força G num eixo de distância responde "quanto de G houve nos 1.200 m", que é
uma pergunta que ninguém faz. A pergunta de engenharia é **como o piloto usa a
aderência disponível**, e ela é bidimensional: o pneu tem um orçamento de
aderência único, repartido entre frear/acelerar (longitudinal) e curvar
(lateral). Gastar tudo num eixo não deixa nada para o outro.

Por isso este gráfico é cartesiano. G lateral em X, longitudinal em Y, e a nuvem
de pontos da volta desenha o envelope que o conjunto carro-pneu-piloto alcançou.
O que se lê nele, e que nenhum gráfico por distância mostra:

- **Um losango** em vez de círculo: o piloto freia reto e curva sem frear.
  Está trocando de fase em vez de combinar, e há tempo parado nos cantos vazios.
- **Envelope cheio à esquerda e vazio à direita** (ou vice-versa): assimetria
  entre curvas de mão esquerda e direita — hábito, ou acerto do carro.
- **Borda superior baixa**: não está usando a frenagem disponível.

A bola marca onde o carro está **agora**. Num rastro estático a nuvem já conta a
história da volta; a bola é o que liga a nuvem ao ponto da pista que o cursor
está examinando, e ao vivo é ela que se move.

Convenção de sinal: longitudinal **positivo é aceleração**, negativo é frenagem,
e o eixo Y é desenhado com o positivo para cima. Frear aparece embaixo, que é
onde a intuição de quem pilota espera — o corpo vai para a frente e para baixo.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Palette, Theme

MARGIN = 26

#: Escala mínima do eixo, em g. Um carro de rua raramente passa de 1,2 g; sem um
#: piso, uma volta lenta encheria o quadro com ruído de ±0,1 g e daria a
#: impressão de estar no limite.
MIN_SCALE_G = 1.2

#: Anéis de referência desenhados ao fundo, em g.
RINGS_G = (0.5, 1.0, 1.5, 2.0)

#: Altura reservada, embaixo, para a leitura numérica.
READOUT_H = 22

#: Raio da bola do carro, em pixels.
BALL_RADIUS = 6.0


class GForceDiagram(QWidget):
    """Círculo de atrito com rastro da volta e indicador do carro."""

    def __init__(self, theme: Theme, *, height: int = 240) -> None:
        super().__init__()
        self._theme = theme
        self._points: list[tuple[float, float]] = []
        self._current: tuple[float, float] | None = None
        self._scale = MIN_SCALE_G

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ---------- dados ----------

    def set_points(self, points: list[tuple[float, float]]) -> None:
        """Define a nuvem da volta: pares (g_lateral, g_longitudinal).

        A escala é calculada **uma vez** aqui, e não a cada repintura. É a mesma
        lição do `DistanceChart`, onde recalcular os limites por ponto tornava a
        pintura O(n²) e travava a interface por quase um segundo numa volta
        inteira.
        """
        self._points = points
        self._scale = self._compute_scale()
        self.update()

    def set_current(self, value: tuple[float, float] | None) -> None:
        """Onde o carro está agora — ou onde o cursor está apontando."""
        self._current = value
        self.update()

    def clear(self) -> None:
        self._points = []
        self._current = None
        self._scale = MIN_SCALE_G
        self.update()

    @property
    def scale_g(self) -> float:
        return self._scale

    @property
    def peak_g(self) -> float:
        """Maior G combinado da volta — o ponto mais distante da origem.

        É o número que resume o gráfico: o quanto de aderência o conjunto
        realmente entregou, somando os dois eixos em vez de olhar um de cada vez.
        """
        if not self._points:
            return 0.0
        return max(math.hypot(lat, lon) for lat, lon in self._points)

    def _compute_scale(self) -> float:
        if not self._points:
            return MIN_SCALE_G
        return max(MIN_SCALE_G, self.peak_g * 1.1)

    # ---------- geometria ----------

    def _plot_rect(self) -> QRectF:
        """Quadrado centrado. **Precisa** ser quadrado.

        Num retângulo, 1 g lateral ocuparia mais pixels que 1 g longitudinal, e
        um envelope circular apareceria como elipse — o gráfico passaria a
        sugerir uma assimetria de aderência que não existe. Aqui a distorção não
        seria estética, seria uma leitura errada.
        """
        # Reserva a faixa de baixo para a leitura numérica antes de medir o
        # quadrado; sem isso o texto era desenhado fora do widget.
        usable_h = self.height() - READOUT_H
        side = max(float(min(self.width(), usable_h) - 2 * MARGIN), 10.0)
        return QRectF(
            (self.width() - side) / 2.0, (usable_h - side) / 2.0, side, side
        )

    def _to_pixel(self, lateral: float, longitudinal: float, rect: QRectF) -> QPointF:
        half = rect.width() / 2.0
        x = rect.center().x() + (lateral / self._scale) * half
        # Sinal invertido: `longitudinal` positivo é aceleração e sobe na tela,
        # mas o Y do Qt cresce para baixo.
        y = rect.center().y() - (longitudinal / self._scale) * half
        return QPointF(x, y)

    # ---------- pintura ----------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette
        rect = self._plot_rect()

        self._paint_grid(painter, rect, palette)
        if not self._points and self._current is None:
            self._paint_placeholder(painter, rect, palette)
            painter.end()
            return

        self._paint_cloud(painter, rect, palette)
        self._paint_ball(painter, rect, palette)
        painter.end()

    def _paint_grid(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        painter.setPen(QPen(QColor(palette.border), 1.0))
        half = rect.width() / 2.0
        center = rect.center()

        for ring in RINGS_G:
            if ring > self._scale:
                continue
            radius = (ring / self._scale) * half
            painter.drawEllipse(center, radius, radius)

        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        painter.drawLine(
            QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y())
        )
        painter.drawLine(
            QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom())
        )

        font = QFont(painter.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(QPen(QColor(palette.text_muted), 1.0))

        # Rótulos nos quatro sentidos. Dizem o que o eixo significa em palavras,
        # porque "longitudinal negativo" não é como ninguém pensa enquanto pilota.
        painter.drawText(
            QRectF(center.x() + 4, rect.top() - 2, half - 4, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "acelera",
        )
        painter.drawText(
            QRectF(center.x() + 4, rect.bottom() - 12, half - 4, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "freia",
        )
        painter.drawText(
            QRectF(rect.left() - MARGIN, center.y() - 16, MARGIN + 46, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "esquerda",
        )
        painter.drawText(
            QRectF(rect.right() - 46, center.y() - 16, MARGIN + 46, 14),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "direita",
        )

        # Na diagonal, e não sobre o eixo horizontal: ali eles caíam em cima
        # de "esquerda"/"direita" e as duas informações viravam uma tarja
        # ilegível. A 45° o anel fica rotulado onde há espaço vazio em
        # praticamente qualquer volta — o envelope raramente enche os cantos.
        diagonal = math.sqrt(0.5)
        for ring in RINGS_G:
            if ring > self._scale:
                continue
            radius = (ring / self._scale) * half
            painter.drawText(
                QRectF(
                    center.x() + radius * diagonal - 16,
                    center.y() - radius * diagonal - 14,
                    32,
                    12,
                ),
                int(Qt.AlignmentFlag.AlignCenter),
                f"{ring:.1f}g",
            )

    def _paint_placeholder(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        painter.setPen(QPen(QColor(palette.text_muted), 1.0))
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignCenter), "sem dados de aderência"
        )

    def _paint_cloud(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        """A nuvem da volta.

        Pontos soltos, não uma linha ligando amostras consecutivas: o carro
        salta de um canto a outro do diagrama entre freada e curva, e ligar isso
        desenharia raios atravessando o meio que sugerem estados pelos quais o
        carro nunca passou.
        """
        if not self._points:
            return

        color = QColor(palette.accent)
        color.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)

        for lateral, longitudinal in self._points:
            painter.drawEllipse(
                self._to_pixel(lateral, longitudinal, rect), 1.6, 1.6
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_ball(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        if self._current is None:
            return

        lateral, longitudinal = self._current
        center = self._to_pixel(lateral, longitudinal, rect)

        # Halo escuro por baixo: sobre a nuvem densa, uma bola sem contorno some.
        halo = QColor(palette.canvas)
        halo.setAlpha(200)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, BALL_RADIUS + 2.5, BALL_RADIUS + 2.5)

        painter.setBrush(QColor(palette.text_primary))
        painter.setPen(QPen(QColor(palette.canvas), 1.5))
        painter.drawEllipse(center, BALL_RADIUS, BALL_RADIUS)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        combined = math.hypot(lateral, longitudinal)
        font = QFont(painter.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        painter.setPen(QPen(QColor(palette.text_secondary), 1.0))
        # Largura do widget, não do quadrado: o quadrado é o menor lado, e a
        # linha de leitura é mais larga que ele — centrada no quadrado, ela
        # saía cortada nas duas pontas.
        painter.drawText(
            QRectF(0.0, rect.bottom() + 2, float(self.width()), 16),
            int(Qt.AlignmentFlag.AlignCenter),
            f"lat {lateral:+.2f}g   long {longitudinal:+.2f}g   "
            f"combinado {combined:.2f}g",
        )
