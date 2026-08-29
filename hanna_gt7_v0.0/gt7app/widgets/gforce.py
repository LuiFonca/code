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

Convenção de sinal: o diagrama marca **para onde o peso vai**, e não para onde o
carro acelera. Freando, o peso vai à frente e o ponto sobe; numa curva à direita
o peso vai à esquerda e o ponto vai à esquerda. É a leitura de quem pilota — o
que o corpo sente — e é como o próprio medidor do GT7 desenha. O motor produz o
oposto exato disso, nos dois eixos (aceleração do carro, que é o certo para a
física e para o banco); a conversão acontece na entrada deste widget, em
`_to_display`, e em lugar nenhum mais.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Palette, Theme

MARGIN = 26

#: Limites de eixo oferecidos, em g. Degraus fixos, e não uma escala colada no
#: pico, porque escala contínua impede comparação: a mesma volta desenha o mesmo
#: envelope em qualquer tamanho de quadro, e duas voltas com picos diferentes
#: viram desenhos incomparáveis. Com degraus, um envelope maior *parece* maior.
SCALE_STEPS_G = (2.0, 3.0, 4.0, 5.0)

#: Menor degrau. Também o piso do modo automático: sem ele, uma volta mansa de
#: 0,3 g encheria o quadro e daria a impressão de estar no limite de aderência.
MIN_SCALE_G = SCALE_STEPS_G[0]

#: Espaçamento dos anéis de referência, em g.
RING_STEP_G = 0.5

#: Altura reservada, embaixo, para a leitura numérica.
READOUT_H = 22

#: Raio da bola do carro, em pixels.
BALL_RADIUS = 6.0


def _to_display(lateral: float, longitudinal: float) -> tuple[float, float]:
    """Da convenção do motor para a do diagrama.

    O `TelemetryEngine` entrega a **aceleração do carro**: longitudinal positivo
    quando ganha velocidade, lateral positivo quando a aceleração aponta para o
    lado direito do carro — ou seja, numa curva à direita. É a convenção certa
    para a física, é a que está gravada no banco e é a que `max_braking_g` lê no
    relatório.

    O diagrama G-G, porém, se lê pelo outro lado: o ponto marca para onde o peso
    é jogado. Freando, o peso vai à frente (ponto sobe); numa curva à direita ele
    vai à esquerda (ponto vai à esquerda). É o oposto exato da aceleração do
    carro, nos dois eixos — daí os dois sinais trocados aqui.

    A conversão fica **na entrada do widget**, e só aqui, por dois motivos.
    Fazê-la no motor trocaria o sinal gravado, misturando duas convenções na
    mesma tabela e invertendo o relatório de frenagem. Fazê-la só na pintura
    deixaria a leitura numérica embaixo do gráfico discordando da posição do
    ponto — um ponto desenhado no topo exibindo "long -1,20 g". Convertendo na
    borda, tudo daqui para dentro fala uma língua só.
    """
    return -lateral, -longitudinal


class GForceDiagram(QWidget):
    """Círculo de atrito com rastro da volta e indicador do carro."""

    def __init__(self, theme: Theme, *, height: int = 240) -> None:
        super().__init__()
        self._theme = theme
        self._points: list[tuple[float, float]] = []
        self._current: tuple[float, float] | None = None
        self._forced_scale: float | None = None
        self._scale = MIN_SCALE_G

        #: Grade e nuvem memorizadas. A nuvem tem a volta inteira — ~6.000
        #: pontos — e era redesenhada a cada movimento do cursor só para a
        #: bola mudar de lugar: 34 ms por evento, medidos. A bola é o único
        #: elemento que se mexe, e é a única coisa pintada agora por
        #: movimento; ver `DistanceChart._render_backdrop`, que resolve o
        #: mesmo problema do mesmo jeito.
        self._backdrop: QPixmap | None = None
        self._backdrop_key: tuple[int, int, float] | None = None

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ---------- dados ----------

    def set_points(self, points: list[tuple[float, float]]) -> None:
        """Define a nuvem da volta: pares (g_lateral, g_longitudinal) do motor.

        Converte para a convenção do diagrama na entrada — ver `_to_display`.

        A escala é calculada **uma vez** aqui, e não a cada repintura. É a mesma
        lição do `DistanceChart`, onde recalcular os limites por ponto tornava a
        pintura O(n²) e travava a interface por quase um segundo numa volta
        inteira.
        """
        self._points = [_to_display(lat, lon) for lat, lon in points]
        self._scale = self._compute_scale()
        self._invalidate_backdrop()

    def set_current(self, value: tuple[float, float] | None) -> None:
        """Onde o carro está agora — ou onde o cursor está apontando."""
        self._current = None if value is None else _to_display(*value)
        self.update()

    def clear(self) -> None:
        self._points = []
        self._current = None
        self._scale = MIN_SCALE_G
        self._invalidate_backdrop()

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

    def set_scale(self, scale_g: float | None) -> None:
        """Fixa o limite dos eixos, ou volta ao automático com `None`."""
        self._forced_scale = scale_g
        self._scale = self._compute_scale()
        self._invalidate_backdrop()

    def _compute_scale(self) -> float:
        """Automático: o **menor degrau que contém** o pico da volta.

        2,8 g desenha num quadro de 3 g; 4,2 g num de 5 g. Escolher o menor que
        cabe é o que mantém o envelope grande na tela sem cortá-lo — e o degrau
        fixo é o que permite comparar duas voltas de olho, porque um envelope
        maior passa a de fato ocupar mais espaço em vez de ser reescalado para
        preencher o mesmo quadro.
        """
        if self._forced_scale is not None:
            return self._forced_scale
        pico = self.peak_g
        for degrau in SCALE_STEPS_G:
            if pico <= degrau:
                return degrau
        # Acima do último degrau, continua subindo no mesmo passo em vez de
        # cortar o dado: um pico de 6 g é implausível num carro, mas se chegar,
        # ver o ponto importa mais que respeitar a lista.
        passo = SCALE_STEPS_G[-1] - SCALE_STEPS_G[-2]
        return SCALE_STEPS_G[-1] + passo * ((pico - SCALE_STEPS_G[-1]) // passo + 1)

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
        """Coordenadas **já na convenção do diagrama** para pixels.

        Quem chama daqui para dentro já passou por `_to_display`; este método não
        troca sinal nenhum de convenção. O único sinal invertido é o do Y, porque
        o Y do Qt cresce para baixo e o positivo do diagrama sobe na tela.
        """
        half = rect.width() / 2.0
        x = rect.center().x() + (lateral / self._scale) * half
        y = rect.center().y() - (longitudinal / self._scale) * half
        return QPointF(x, y)

    # ---------- pintura ----------

    def _invalidate_backdrop(self) -> None:
        self._backdrop = None
        self._backdrop_key = None
        self.update()

    def _render_backdrop(self) -> QPixmap:
        """Grade e nuvem numa imagem, na densidade do dispositivo."""
        densidade = self.devicePixelRatioF()
        imagem = QPixmap(
            max(1, int(self.width() * densidade)),
            max(1, int(self.height() * densidade)),
        )
        imagem.setDevicePixelRatio(densidade)
        imagem.fill(Qt.GlobalColor.transparent)

        palette = self._theme.palette
        painter = QPainter(imagem)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._plot_rect()
        self._paint_grid(painter, rect, palette)
        if self._points:
            self._paint_cloud(painter, rect, palette)
        painter.end()
        return imagem

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        del event
        chave = (self.width(), self.height(), self.devicePixelRatioF())
        if self._backdrop is None or self._backdrop_key != chave:
            self._backdrop = self._render_backdrop()
            self._backdrop_key = chave

        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._backdrop)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette
        rect = self._plot_rect()

        if not self._points and self._current is None:
            self._paint_placeholder(painter, rect, palette)
            painter.end()
            return

        self._paint_ball(painter, rect, palette)
        painter.end()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (API do Qt)
        self._invalidate_backdrop()
        super().resizeEvent(event)

    def _paint_grid(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        painter.setPen(QPen(QColor(palette.border), 1.0))
        half = rect.width() / 2.0
        center = rect.center()

        for ring in self._rings():
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
        #
        # Freia em cima e acelera embaixo: o diagrama mostra para onde o peso vai,
        # e freando o peso vai à frente. Os rótulos precisam acompanhar a
        # convenção — com eles trocados, o gráfico afirma o contrário do que
        # desenha, e nada na tela denuncia.
        painter.drawText(
            QRectF(center.x() + 4, rect.top() - 2, half - 4, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "freia",
        )
        painter.drawText(
            QRectF(center.x() + 4, rect.bottom() - 12, half - 4, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "acelera",
        )
        # No lado lateral o rótulo nomeia a **curva**, não o lado da tela. Dizer
        # só "esquerda" aqui reabriria a confusão pelo outro lado: numa curva à
        # direita o peso vai à esquerda, e quem virasse à direita veria a bola
        # cair sob a palavra "esquerda" sem entender por quê.
        painter.drawText(
            QRectF(rect.left() - MARGIN, center.y() - 16, MARGIN + 46, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "curva à dir.",
        )
        painter.drawText(
            QRectF(rect.right() - 46, center.y() - 16, MARGIN + 46, 14),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "curva à esq.",
        )

        # Na diagonal, e não sobre o eixo horizontal: ali eles caíam em cima
        # de "esquerda"/"direita" e as duas informações viravam uma tarja
        # ilegível. A 45° o anel fica rotulado onde há espaço vazio em
        # praticamente qualquer volta — o envelope raramente enche os cantos.
        diagonal = math.sqrt(0.5)
        for ring in self._rings():
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

    def _rings(self) -> list[float]:
        """Anéis de meio em meio g, até o limite — e nunca mais que cinco.

        Num quadro de 5 g, um anel a cada 0,5 g são dez circunferências: o fundo
        vira hachura e a nuvem some dentro dela. Acima de cinco anéis o passo
        dobra.
        """
        passo = RING_STEP_G
        while self._scale / passo > 5:
            passo *= 2
        anel = passo
        aneis: list[float] = []
        while anel <= self._scale + 1e-9:
            aneis.append(anel)
            anel += passo
        return aneis

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
