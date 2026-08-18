"""
Faixa de atuação dos auxílios — onde o TCS e o ASM entraram.

Uma linha por auxílio, no mesmo eixo X dos canais logo acima, com um bloco
pintado em cada trecho de atuação. É deliberadamente **não** um gráfico de
linha: o dado é binário e localizado, e uma linha subindo e descendo entre 0 e 1
gastaria a altura de um canal inteiro para dizer o que um bloco diz numa tira.

O alinhamento com o `DistanceChart` é a coisa que precisa estar certa aqui. As
margens saem das mesmas constantes que ele usa, e a faixa recebe a mesma janela
de X — sem isso, o bloco cairia alguns pixels ao lado do ponto do gráfico em que
o auxílio atuou, e a leitura combinada (que é a razão de existir da faixa)
passaria a mentir.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Palette, Theme
from .charts import MARGIN_LEFT, MARGIN_RIGHT

#: Altura de cada tira, em pixels.
ROW_H = 18

#: Espaço entre tiras.
ROW_GAP = 6

#: Largura mínima de um bloco pintado, em pixels.
#:
#: Uma atuação de 40 ms numa volta de 90 s é menos de um pixel — pintada fiel à
#: escala, ela some, e a faixa passa a afirmar que o auxílio não atuou. Um piso
#: de largura mantém o episódio visível; a alternativa (sumir) é a que mente.
MIN_BLOCK_W = 2.0


class AidBand(QWidget):
    """Tiras de atuação, alinhadas ao eixo X dos gráficos de canal."""

    def __init__(
        self,
        theme: Theme,
        *,
        aids: tuple[str, ...] = ("TCS", "ASM"),
        colors: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._theme = theme
        self._aids = aids
        #: Rótulo exibido, separado da chave da linha.
        #:
        #: A calha do rótulo tem a largura da margem esquerda dos gráficos — ela
        #: **precisa** ter, senão a faixa deixa de alinhar com o eixo X deles, e
        #: um bloco de atuação cairia ao lado do ponto do gráfico em que o
        #: auxílio atuou. Em 46 px cabe "TCS", não "TCS comp.": na comparação os
        #: rótulos saíam truncados em "; comp." e "iSM ref.". A saída é a linha
        #: dizer o auxílio e a **cor** dizer a volta, que já é a convenção da
        #: página inteira.
        self._labels = labels or {}
        #: Cor por linha. Na análise a linha identifica o **auxílio**; na
        #: comparação identifica a **volta**, porque lá há quatro linhas (dois
        #: auxílios × duas voltas) e a pergunta é qual volta acionou mais.
        self._colors = colors or {}
        self._spans: dict[str, list[tuple[float, float]]] = {a: [] for a in aids}
        self._min_x = 0.0
        self._max_x = 1.0
        self._cursor: float | None = None
        self._note = ""

        altura = len(aids) * ROW_H + (len(aids) - 1) * ROW_GAP + 20
        self.setMinimumHeight(altura)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ---------- dados ----------

    def set_spans(
        self,
        spans: dict[str, list[tuple[float, float]]],
        *,
        x_range: tuple[float, float],
    ) -> None:
        """Trechos por auxílio, como pares (x inicial, x final).

        A janela de X vem de fora, do gráfico com que esta faixa se alinha. Ela
        **não** é deduzida dos próprios trechos: uma volta em que o TCS só atua
        nos últimos 200 m daria uma janela de 200 m e desenharia a atuação
        ocupando a largura inteira, no lugar errado.
        """
        self._spans = {aid: list(spans.get(aid, [])) for aid in self._aids}
        low, high = x_range
        self._min_x = low
        self._max_x = high if high > low else low + 1.0
        self.update()

    def set_cursor(self, x_value: float | None) -> None:
        if self._cursor != x_value:
            self._cursor = x_value
            self.update()

    def set_note(self, note: str) -> None:
        """Texto no lugar das tiras — para "não foi gravado", que não é vazio."""
        self._note = note
        self.update()

    def clear(self) -> None:
        self._spans = {aid: [] for aid in self._aids}
        self._cursor = None
        self._note = ""
        self.update()

    def active_at(self, x_value: float) -> list[str]:
        """Quais auxílios estão atuando na posição informada."""
        return [
            aid
            for aid, spans in self._spans.items()
            if any(start <= x_value <= end for start, end in spans)
        ]

    # ---------- geometria ----------

    def _x_pixel(self, x_value: float) -> float:
        largura = max(float(self.width() - MARGIN_LEFT - MARGIN_RIGHT), 1.0)
        span = self._max_x - self._min_x
        return MARGIN_LEFT + (x_value - self._min_x) / span * largura

    def _row_rect(self, index: int) -> QRectF:
        topo = 16 + index * (ROW_H + ROW_GAP)
        return QRectF(
            float(MARGIN_LEFT),
            float(topo),
            max(float(self.width() - MARGIN_LEFT - MARGIN_RIGHT), 1.0),
            float(ROW_H),
        )

    # ---------- pintura ----------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette

        font = QFont(painter.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)

        if self._note:
            painter.setPen(QPen(QColor(palette.text_muted), 1.0))
            painter.drawText(
                QRectF(0, 0, float(self.width()), float(self.height())),
                int(Qt.AlignmentFlag.AlignCenter),
                self._note,
            )
            painter.end()
            return

        for index, aid in enumerate(self._aids):
            self._paint_row(painter, index, aid, palette)

        self._paint_cursor(painter, palette)
        painter.end()

    def _paint_row(
        self, painter: QPainter, index: int, aid: str, palette: Palette
    ) -> None:
        rect = self._row_rect(index)

        painter.setPen(QPen(QColor(palette.text_muted), 1.0))
        painter.drawText(
            QRectF(0.0, rect.top(), float(MARGIN_LEFT - 6), rect.height()),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            self._labels.get(aid, aid),
        )

        # O leito da tira, sempre desenhado: sem ele, "o TCS não atuou" e "o TCS
        # não está nesta tela" ficam com a mesma aparência — nada.
        painter.setPen(Qt.PenStyle.NoPen)
        leito = QColor(palette.border)
        leito.setAlpha(70)
        painter.setBrush(leito)
        painter.drawRoundedRect(rect, 3.0, 3.0)

        cor = QColor(
            self._colors.get(
                aid, palette.yellow if aid.startswith("TCS") else palette.purple
            )
        )
        painter.setBrush(cor)
        for start, end in self._spans.get(aid, []):
            x0 = self._x_pixel(start)
            x1 = self._x_pixel(end)
            largura = max(x1 - x0, MIN_BLOCK_W)
            painter.drawRoundedRect(
                QRectF(x0, rect.top(), largura, rect.height()), 3.0, 3.0
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_cursor(self, painter: QPainter, palette: Palette) -> None:
        if self._cursor is None:
            return
        x = self._x_pixel(self._cursor)
        painter.setPen(QPen(QColor(palette.text_secondary), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(
            int(x), 12, int(x), int(self._row_rect(len(self._aids) - 1).bottom())
        )
