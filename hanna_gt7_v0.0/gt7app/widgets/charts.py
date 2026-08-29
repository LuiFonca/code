"""
Gráficos desenhados com QPainter.

Por que não QtCharts
--------------------
QtCharts está disponível e a aplicação anterior o usava. A troca não é gosto: a
Fase 5 existe para que a aplicação tenha **uma** linguagem visual, e o QtCharts
traz a sua — margens, fontes de eixo, cor de grade e antialiasing próprios, que
só se dobram aos tokens até certo ponto. Misturar os dois produz telas que quase
combinam, que é pior do que duas telas assumidamente diferentes.

Desenhar à mão custa ~200 linhas e entrega controle total: cada pixel sai de
`tokens.py`. O que se perde é a interação rica que o QtCharts dá de graça (zoom,
pan); o cursor sincronizado, que é o que a análise de volta realmente exige,
está implementado aqui.

Eixo de distância, não de tempo
-------------------------------
Todos os gráficos de volta usam **distância** no eixo X. É a mesma decisão do
`LapComparator`: comparando por tempo, um trecho onde o piloto freou mais cedo
desalinha tudo o que vem depois. Duas voltas sobrepostas por distância mostram a
diferença no ponto em que ela acontece.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Palette, Theme

# Margens internas do gráfico. A esquerda é maior porque abriga os rótulos do
# eixo Y; a inferior, os de distância.
#: Escala de velocidade: começa em 0–300 km/h e sobe de 100 em 100 se o carro
#: passar disso. Um teto fixo é o que torna duas voltas comparáveis de olho —
#: com escala colada no pico, a mesma curva parece agressiva numa volta lenta.
SPEED_TOP_MIN_KMH = 300.0
SPEED_STEP_KMH = 100.0

MARGIN_LEFT = 46
MARGIN_RIGHT = 12
MARGIN_TOP = 22
MARGIN_BOTTOM = 22


def _format_x(value: float, unit: str) -> str:
    """Rótulo do eixo X. Segundos pedem uma casa; metros, nenhuma.

    Com `.0f` em ambos, uma janela de 8 s virava "0 s, 2 s, 4 s, 6 s, 8 s" e
    perdia justamente a resolução que faz o eixo de tempo valer a pena.
    """
    if unit == "s":
        return f"{value:.1f} {unit}"
    return f"{value:.0f} {unit}"


def _step_up(value: float, step: float, minimum: float | None) -> float:
    """Arredonda para cima até o próximo múltiplo de `step`, com piso.

    `_step_up(197, 100, 300)` → 300; `_step_up(317, 100, 300)` → 400. É o que
    faz a escala de velocidade subir de 100 em 100 em vez de colar no pico.
    """
    top = minimum if minimum is not None else step
    while value > top:
        top += step
    return top


@dataclass(slots=True)
class Series:
    """Uma curva do gráfico: pares (distância em m, valor)."""

    label: str
    color: str
    points: list[tuple[float, float]] = field(default_factory=list)
    width: float = 1.8
    dashed: bool = False

    @property
    def is_empty(self) -> bool:
        return len(self.points) < 2


class DistanceChart(QWidget):
    """Gráfico de linhas com eixo X em metros e cursor sincronizável.

    Emite `hovered` com a distância sob o mouse, e `hover_left` ao sair. Vários
    gráficos empilhados se sincronizam ligando o sinal de um ao
    `set_cursor()` dos outros — é assim que a página de análise mantém
    velocidade, freio e acelerador lendo o mesmo ponto da pista.
    """

    hovered = Signal(float)
    hover_left = Signal()
    clicked = Signal(float)

    def __init__(
        self,
        theme: Theme,
        title: str = "",
        *,
        unit: str = "",
        height: int = 150,
        y_range: tuple[float, float] | None = None,
        y_step: float | None = None,
        y_top_min: float | None = None,
        y_symmetric: bool = False,
        x_unit: str = "m",
    ) -> None:
        super().__init__()
        self._theme = theme
        self._title = title
        self._unit = unit
        self._x_unit = x_unit
        self._forced_range = y_range

        #: Degrau da escala vertical, e teto mínimo. Com `y_step=100` e
        #: `y_top_min=300`, uma volta que chega a 197 km/h desenha até 300 e uma
        #: que chega a 317 desenha até 400. Existe porque escala automática
        #: mente por omissão: a mesma curva de velocidade parece agressiva numa
        #: volta lenta e mansa numa rápida, e comparar duas voltas de olho vira
        #: impossível. Degrau fixo mantém o traço legível **e** comparável.
        self._y_step = y_step
        self._y_top_min = y_top_min
        #: Eixo espelhado em torno do zero, para canais **com sinal**.
        #:
        #: Guinada e volante têm lado: positivo é direita, negativo é
        #: esquerda. Com o eixo assimétrico — que é o que o degrau produz
        #: sozinho, porque o teto mínimo só se aplica em cima — uma curva à
        #: direita de 30° desenha o dobro da altura de uma curva à esquerda
        #: de 30°, e o gráfico passa a afirmar uma assimetria de pilotagem
        #: que não existe. Num canal que só sobe (velocidade, pedais,
        #: aderência) espelhar seria desperdiçar metade da altura, e por
        #: isso isto é opção e não regra.
        self._y_symmetric = y_symmetric
        self._series: list[Series] = []
        # Faixa vertical memorizada. Recalculada só quando as séries mudam —
        # ver a nota em `_y_bounds`.
        self._bounds: tuple[float, float] = y_range or (0.0, 1.0)
        self._cursor_m: float | None = None
        #: Janela fixa do eixo X, ou None para seguir os dados. Ver
        #: `set_x_window`.
        self._x_window: tuple[float, float] | None = None
        self._min_x = 0.0
        self._max_distance = 0.0
        # Amplitude memorizada, como `_bounds`. Era uma `@property`, e property
        # é chamada de descritor: dentro do laço de pintura, com ~6.000 pontos e
        # duas séries, custou 13× em vez dos 10× do crescimento dos dados e
        # derrubou o teste que existe justamente para pegar isso.
        self._x_span = 1.0
        self._markers: list[tuple[float, str, str]] = []
        #: Cursor travado por clique. Só muda a **aparência** do cursor; quem
        #: decide ignorar o movimento do mouse é a página, porque o cursor é
        #: compartilhado entre vários gráficos e o mapa, e cada um decidindo por
        #: si acabaria com metade travada e metade seguindo o ponteiro.
        self._cursor_locked = False

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    # ---------- dados ----------

    def set_x_window(self, window: tuple[float, float] | None) -> None:
        """Fixa o eixo X numa janela, em vez de deixá-lo seguir os dados.

        Ao vivo isto é questão de fidelidade, não de estética. Com o eixo
        seguindo os dados, os primeiros segundos de captura desenham uma
        janela de 1 s, depois 2 s, depois 3 — o traço parece percorrer a
        largura toda enquanto na verdade só existe um instante de dado, e a
        escala horizontal muda embaixo do olho a cada repintura. Com a
        janela fixa, o que não foi medido aparece como **espaço vazio**, que
        é a representação honesta de ausência.

        `None` devolve o comportamento elástico, que é o certo na Análise:
        ali a volta inteira já existe e o eixo deve cobri-la.
        """
        self._x_window = window
        self._recompute_x()
        self.update()

    def _recompute_x(self) -> None:
        if self._x_window is not None:
            inicio, fim = self._x_window
            self._min_x = inicio
            self._max_distance = fim
            self._x_span = (fim - inicio) or 1.0
            return

        self._max_distance = max(
            (s.points[-1][0] for s in self._series if not s.is_empty), default=0.0
        )
        # O eixo começa onde os dados começam, não em zero. Ao vivo o rastro
        # guarda só os últimos 800 m, então aos 1.500 m os dados vão de 700 a
        # 1.500 — ancorado em zero, o gráfico desenhava metade vazia e o traço
        # se espremia na direita. Era o "gráfico pela metade" relatado.
        self._min_x = min(
            (s.points[0][0] for s in self._series if not s.is_empty), default=0.0
        )
        self._x_span = (self._max_distance - self._min_x) or 1.0

    def set_series(self, series: list[Series]) -> None:
        self._series = series
        self._recompute_x()
        self._bounds = self._y_bounds()
        self.update()

    def set_x_unit(self, unit: str) -> None:
        """Troca o rótulo do eixo X (m ou s). Quem troca também troca os dados."""
        if self._x_unit != unit:
            self._x_unit = unit
            self.update()

    def set_markers(self, markers: list[tuple[float, str, str]]) -> None:
        """Marcas verticais: (distância, rótulo, cor). Usado para ápices e setores."""
        self._markers = markers
        self.update()

    def set_cursor(self, distance_m: float | None) -> None:
        if self._cursor_m != distance_m:
            self._cursor_m = distance_m
            self.update()

    def clear(self) -> None:
        self._series = []
        self._markers = []
        self._cursor_m = None
        self._recompute_x()
        if self._x_window is None:
            self._min_x = 0.0
            self._max_distance = 0.0
            self._x_span = 1.0
        self._bounds = self._forced_range or (0.0, 1.0)
        self.update()

    @property
    def is_empty(self) -> bool:
        return not self._series or all(s.is_empty for s in self._series)

    def value_at(self, distance_m: float) -> list[tuple[Series, float]]:
        """Valor de cada série na distância informada, interpolado."""
        found: list[tuple[Series, float]] = []
        for series in self._series:
            value = _interpolate(series.points, distance_m)
            if value is not None:
                found.append((series, value))
        return found

    # ---------- geometria ----------

    def _plot_rect(self) -> QRectF:
        return QRectF(
            MARGIN_LEFT,
            MARGIN_TOP,
            max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )

    def _y_bounds(self) -> tuple[float, float]:
        """Faixa vertical do gráfico.

        **Percorre todas as séries inteiras**, então é O(n) e não pode ser
        chamada por ponto. O resultado é memorizado em `set_series`; quem pinta
        usa `self._bounds`.

        Isto já foi um defeito real e caro: `_to_pixel` chamava esta função a
        cada ponto, o que tornava a repintura quadrática. Com ~6000 amostras por
        volta e duas séries, uma repintura levava **800 ms** — meio segundo de
        janela congelada toda vez que qualquer coisa mudava o layout da página.
        Passou despercebido enquanto nada além da troca de volta forçava
        repintura; apareceu na Fase 8, quando o cartão do engenheiro passou a
        crescer no meio da tela ao receber a resposta.
        """
        if self._forced_range is not None:
            return self._forced_range

        values = [v for s in self._series for _, v in s.points]
        if not values:
            return 0.0, 1.0

        low, high = min(values), max(values)

        # **Zero sempre no eixo.** Sem isso, uma volta cuja velocidade varia de
        # 180 a 200 km/h desenha uma serra dramática entre esses dois valores, e
        # os 20 km/h de variação ocupam a altura toda. O olho lê inclinação, não
        # números: escala flutuante transforma ruído em drama e faz duas voltas
        # parecidas parecerem diferentes. Ancorar em zero devolve a proporção.
        low = min(0.0, low)
        high = max(0.0, high)

        if self._y_symmetric:
            extremo = max(abs(low), abs(high))
            if self._y_step:
                extremo = _step_up(extremo, self._y_step, self._y_top_min)
            elif extremo < 1e-9:
                extremo = 1.0
            return -extremo, extremo

        if self._y_step:
            high = _step_up(high, self._y_step, self._y_top_min)
            if low < 0:
                low = -_step_up(-low, self._y_step, None)
            return low, high

        if high - low < 1e-9:
            return low, high + 1.0
        # Uma folga de 8% no topo impede que o pico encoste na borda, onde
        # ficaria visualmente cortado. O piso fica colado no zero de propósito.
        return low, high + (high - low) * 0.08

    def _x_pixel(self, x_value: float, rect: QRectF) -> float:
        """Valor do eixo X → pixel. **Uma** fórmula, quatro chamadores.

        Estava repetida em `_to_pixel`, nos rótulos, nos marcadores e no cursor.
        Repetir a conta é como o cursor um dia apontaria para um lugar diferente
        do traço — e nada na tela diria qual dos dois está certo.
        """
        return rect.left() + ((x_value - self._min_x) / self._x_span) * rect.width()

    def _to_pixel(self, distance_m: float, value: float, rect: QRectF) -> QPointF:
        low, high = self._bounds
        span = high - low or 1.0
        y = rect.bottom() - ((value - low) / span) * rect.height()
        return QPointF(self._x_pixel(distance_m, rect), y)

    def _to_distance(self, x_pixel: float, rect: QRectF) -> float:
        ratio = (x_pixel - rect.left()) / (rect.width() or 1.0)
        ratio = max(0.0, min(1.0, ratio))
        return self._min_x + ratio * self._x_span

    # ---------- pintura ----------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette
        rect = self._plot_rect()

        painter.fillRect(self.rect(), QColor(palette.surface))
        self._paint_frame(painter, rect, palette)

        if self.is_empty:
            self._paint_placeholder(painter, rect, palette)
            painter.end()
            return

        self._paint_markers(painter, rect)
        self._paint_series(painter, rect)
        self._paint_cursor(painter, rect, palette)
        painter.end()

    def _paint_frame(self, painter: QPainter, rect: QRectF, palette: Palette) -> None:
        font = QFont(self._theme.type_scale.family_ui.split(",")[0].strip("'"))
        font.setPixelSize(self._theme.type_scale.micro)
        painter.setFont(font)

        # Grade horizontal: quatro divisões bastam para dar referência sem
        # transformar o fundo em papel milimetrado.
        painter.setPen(QPen(QColor(palette.border), 1))
        low, high = self._bounds
        for i in range(5):
            y = rect.bottom() - (i / 4) * rect.height()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

            value = low + (i / 4) * (high - low)
            painter.setPen(QPen(QColor(palette.text_muted), 1))
            painter.drawText(
                QRectF(0, y - 8, MARGIN_LEFT - 6, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _compact(value),
            )
            painter.setPen(QPen(QColor(palette.border), 1))

        if self._title:
            painter.setPen(QPen(QColor(palette.text_muted), 1))
            title_font = QFont(font)
            title_font.setPixelSize(self._theme.type_scale.label)
            title_font.setBold(True)
            painter.setFont(title_font)
            label = f"{self._title}  ({self._unit})" if self._unit else self._title
            painter.drawText(QPointF(MARGIN_LEFT, MARGIN_TOP - 8), label)
            painter.setFont(font)

        # Rótulos de distância no eixo X.
        if self._max_distance > 0:
            painter.setPen(QPen(QColor(palette.text_muted), 1))
            for i in range(5):
                distance = self._min_x + (i / 4) * self._x_span
                x = rect.left() + (i / 4) * rect.width()
                # Prende o rótulo dentro do widget: centrado, o primeiro e o
                # último sangram para fora e aparecem cortados ("3799 r").
                left = min(max(0.0, x - 30), self.width() - 60.0)
                painter.drawText(
                    QRectF(left, rect.bottom() + 4, 60, 14),
                    int(Qt.AlignmentFlag.AlignCenter),
                    _format_x(distance, self._x_unit),
                )

    def _paint_placeholder(
        self, painter: QPainter, rect: QRectF, palette: Palette
    ) -> None:
        painter.setPen(QPen(QColor(palette.text_muted), 1))
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignCenter), "sem dados para exibir"
        )

    def _paint_series(self, painter: QPainter, rect: QRectF) -> None:
        for series in self._series:
            if series.is_empty:
                continue
            pen = QPen(QColor(series.color), series.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if series.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)

            # Reamostra para a largura em pixels: uma volta tem ~6000 amostras e
            # o gráfico ~900 px, então desenhar tudo gasta tempo para pintar
            # várias vezes o mesmo pixel.
            step = max(1, len(series.points) // max(1, int(rect.width())))
            path = [
                self._to_pixel(d, v, rect) for d, v in series.points[::step]
            ]
            if len(path) > 1:
                painter.drawPolyline(path)

    def _paint_markers(self, painter: QPainter, rect: QRectF) -> None:
        for distance, label, color in self._markers:
            if self._max_distance <= 0:
                continue
            x = self._x_pixel(distance, rect)
            pen = QPen(QColor(color), 1)
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if label:
                painter.drawText(QPointF(x + 3, rect.top() + 10), label)

    def _paint_cursor(self, painter: QPainter, rect: QRectF, palette: Palette) -> None:
        if self._cursor_m is None or self._max_distance <= 0:
            return

        x = self._x_pixel(self._cursor_m, rect)
        cor = palette.accent if self._cursor_locked else palette.text_secondary
        painter.setPen(QPen(QColor(cor), 1.6 if self._cursor_locked else 1.0))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        readings = self.value_at(self._cursor_m)
        if not readings:
            return

        # Caixa de leitura, ancorada do lado que tiver espaço.
        font = QFont(self._theme.type_scale.family_mono.split(",")[0].strip("'"))
        font.setPixelSize(self._theme.type_scale.micro)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        lines = [f"{s.label} {value:.1f}" for s, value in readings]
        box_width = max(metrics.horizontalAdvance(line) for line in lines) + 12
        box_height = len(lines) * 13 + 8
        box_x = x + 8 if x + box_width + 12 < rect.right() else x - box_width - 8
        box = QRectF(box_x, rect.top() + 4, box_width, box_height)

        painter.fillRect(box, QColor(palette.surface_overlay))
        painter.setPen(QPen(QColor(palette.border_strong), 1))
        painter.drawRect(box)

        for index, (series, value) in enumerate(readings):
            painter.setPen(QPen(QColor(series.color), 1))
            painter.drawText(
                QPointF(box.left() + 6, box.top() + 14 + index * 13),
                f"{series.label} {value:.1f}",
            )

    # ---------- interação ----------

    def set_cursor_locked(self, locked: bool) -> None:
        """Marca o cursor como travado — linha cheia e mais viva.

        Sem sinal visual, um cursor que parou de seguir o mouse parece a
        aplicação ter travado. É a diferença entre um recurso e um defeito.
        """
        if self._cursor_locked != locked:
            self._cursor_locked = locked
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802  (API do Qt)
        rect = self._plot_rect()
        if rect.contains(event.position()):
            distance = self._to_distance(event.position().x(), rect)
            self.hovered.emit(distance)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802  (API do Qt)
        rect = self._plot_rect()
        if rect.contains(event.position()):
            self.clicked.emit(self._to_distance(event.position().x(), rect))
        super().mousePressEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802  (API do Qt)
        # Não limpa o cursor aqui: quem manda no cursor é a página, que o
        # compartilha entre gráficos e mapa. Limpando localmente, mover o mouse
        # de um gráfico para o vizinho apagava o cursor do primeiro no meio do
        # gesto — e com o cursor travado ele se perderia ao sair do widget,
        # que é justamente o contrário do que travar quer dizer.
        self.hover_left.emit()
        super().leaveEvent(event)  # type: ignore[arg-type]


def _interpolate(points: list[tuple[float, float]], x: float) -> float | None:
    """Valor em `x` por interpolação linear. None fora do intervalo coberto."""
    if len(points) < 2 or x < points[0][0] or x > points[-1][0]:
        return None

    low, high = 0, len(points) - 1
    while low < high - 1:
        middle = (low + high) // 2
        if points[middle][0] <= x:
            low = middle
        else:
            high = middle

    x0, y0 = points[low]
    x1, y1 = points[high]
    if x1 == x0:
        return y0
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def _compact(value: float) -> str:
    """Rótulo curto de eixo: sem casas decimais quando não fazem falta."""
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"
