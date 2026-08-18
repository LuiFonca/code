"""
Mapa de pista — o traçado desenhado a partir das coordenadas X/Z.

O GT7 não transmite um mapa; transmite a posição do carro. O traçado é o
rastro dessa posição ao longo de uma volta, e é isso que se desenha aqui.

O widget aceita **vários traçados sobrepostos**, que é o que dá sentido à
comparação: duas voltas no mesmo mapa mostram onde as linhas divergem. Aceita
também marcadores — usados para plotar os ápices detectados na Fase 4 e os
limites de setor.

O enquadramento preserva a proporção. Esticar o traçado para preencher o widget
deformaria a geometria da pista, e uma curva de raio constante pareceria oval —
o que confunde exatamente quem está tentando ler a linha.

Mapa de calor
-------------
Um traçado pode ser pintado por **magnitude** (velocidade), com uma escala
sequencial de uma cor só. Uma cor só, e nunca arco-íris: num arco-íris o leitor
não sabe se verde é mais ou menos que laranja sem consultar a legenda, e a
ordem deixa de estar na cor. A escala vem de `tokens.SequentialRamp`, validada
contra a superfície de cada tema.

Sincronia com os gráficos
-------------------------
O mapa é a outra metade da leitura: os gráficos mostram *o que* aconteceu, o
mapa mostra *onde*. `set_cursor()` recebe uma distância e marca a posição
correspondente; o clique e o passar do mouse fazem o caminho inverso, emitindo
`hovered` com a distância sob o ponteiro. As páginas ligam os dois lados.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Theme

PADDING = 18

# Altura da barra de legenda do mapa de calor.
LEGEND_HEIGHT = 10
LEGEND_WIDTH = 130

# Segmentos do mapa de calor. Mais que isto não muda o que se vê e custa
# travessias; menos deixa a transição de cor em degraus visíveis.
HEATMAP_SEGMENTS = 240


@dataclass(slots=True)
class TrackPath:
    """Um traçado: pontos (x, z) e como desenhá-lo.

    `values` e `distances`, quando presentes, são paralelos a `points`:
    `values` habilita o mapa de calor, `distances` habilita o cursor e a
    interação por clique.
    """

    label: str
    color: str
    points: list[tuple[float, float]] = field(default_factory=list)
    width: float = 2.0
    dashed: bool = False
    values: list[float] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    """Cor explícita por ponto, para canais **categóricos**.

    Um gradiente responde "quanto"; há canais em que a pergunta é "qual" —
    acelerador, freio ou nenhum dos dois. Interpolar entre três estados
    inventaria um quarto: metade do caminho entre verde e vermelho é laranja, e
    laranja aqui significaria uma situação que não existe. Por isso a cor vem
    pronta, e não de uma rampa.
    """

    @property
    def is_empty(self) -> bool:
        return len(self.points) < 2

    @property
    def has_heatmap(self) -> bool:
        return len(self.values) == len(self.points) and len(self.values) > 1

    @property
    def has_categorical(self) -> bool:
        return len(self.colors) == len(self.points) and len(self.colors) > 1

    @property
    def is_locatable(self) -> bool:
        return len(self.distances) == len(self.points) and len(self.distances) > 1

    def index_at_distance(self, distance_m: float) -> int | None:
        """Índice do ponto mais próximo da distância informada."""
        if not self.is_locatable:
            return None
        index = bisect.bisect_left(self.distances, distance_m)
        if index <= 0:
            return 0
        if index >= len(self.distances):
            return len(self.distances) - 1
        before = self.distances[index - 1]
        after = self.distances[index]
        return index - 1 if distance_m - before <= after - distance_m else index


@dataclass(slots=True)
class TrackMarker:
    """Um ponto de interesse sobre o traçado."""

    x: float
    z: float
    color: str
    label: str = ""
    radius: float = 4.0
    hollow: bool = False


class TrackMap(QWidget):
    """Desenha um ou mais traçados com proporção preservada."""

    hovered = Signal(float)
    hover_left = Signal()
    clicked = Signal(float)

    def __init__(
        self,
        theme: Theme,
        *,
        height: int = 260,
        heatmap_label: str = "",
    ) -> None:
        super().__init__()
        self._theme = theme
        self._paths: list[TrackPath] = []
        self._markers: list[TrackMarker] = []
        self._bounds: tuple[float, float, float, float] | None = None
        self._cursor_m: float | None = None
        self._heatmap_range: tuple[float, float] | None = None
        self._heatmap_label = heatmap_label
        self._legend: list[tuple[str, str]] = []

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def _segment_budget(self, *, per_pixel: float = 1.0) -> int:
        """Quantos segmentos desenhar, proporcional ao tamanho na tela.

        Era um teto fixo de 1.200 pontos, escolhido para não repintar o mesmo
        pixel dezenas de vezes num widget pequeno. Num mapa grande ele passou a
        cortar informação de verdade: duas linhas de corrida separadas por meio
        metro só se distinguem se cada uma tiver amostras suficientes para o
        desvio aparecer entre dois pixels vizinhos. Reamostrado a 1.200, o
        desvio caía dentro de um segmento e as duas viravam a mesma linha.

        Amarrando o orçamento ao perímetro do widget, o mapa pequeno continua
        barato e o grande fica fiel — sem número mágico que sirva mal aos dois.
        """
        perimetro = 2.0 * (self.width() + self.height())
        return max(400, int(perimetro * per_pixel))

    def set_legend(self, entries: list[tuple[str, str]]) -> None:
        """Legenda discreta: pares (cor, rótulo). Vazia volta à rampa."""
        self._legend = entries
        self.update()

    # ---------- dados ----------

    def set_paths(self, paths: list[TrackPath]) -> None:
        self._paths = paths
        self._recompute_bounds()
        self._recompute_heatmap_range()
        self.update()

    def set_markers(self, markers: list[TrackMarker]) -> None:
        self._markers = markers
        self.update()

    def set_cursor(self, distance_m: float | None) -> None:
        if self._cursor_m != distance_m:
            self._cursor_m = distance_m
            self.update()

    def clear(self) -> None:
        self._paths = []
        self._markers = []
        self._bounds = None
        self._cursor_m = None
        self._heatmap_range = None
        self._legend = []
        self.update()

    @property
    def is_empty(self) -> bool:
        return not self._paths or all(p.is_empty for p in self._paths)

    def _recompute_bounds(self) -> None:
        xs = [x for path in self._paths for x, _ in path.points]
        zs = [z for path in self._paths for _, z in path.points]
        self._bounds = (min(xs), min(zs), max(xs), max(zs)) if xs and zs else None

    def _recompute_heatmap_range(self) -> None:
        values = [v for path in self._paths if path.has_heatmap for v in path.values]
        if not values:
            self._heatmap_range = None
            return
        low, high = min(values), max(values)
        self._heatmap_range = (low, high) if high > low else None

    # ---------- geometria ----------

    def _plot_rect(self) -> QRectF:
        bottom = PADDING + (LEGEND_HEIGHT + 14 if self._heatmap_range else 0)
        return QRectF(
            PADDING,
            PADDING,
            max(1.0, self.width() - 2 * PADDING),
            max(1.0, self.height() - PADDING - bottom),
        )

    def _project(self, x: float, z: float, rect: QRectF) -> QPointF:
        """Coordenada de mundo → pixel, com escala isotrópica.

        A mesma escala nos dois eixos é o que preserva a forma da pista; o
        traçado é então centralizado no espaço que sobra.
        """
        assert self._bounds is not None
        min_x, min_z, max_x, max_z = self._bounds
        span_x = max(max_x - min_x, 1e-6)
        span_z = max(max_z - min_z, 1e-6)

        scale = min(rect.width() / span_x, rect.height() / span_z)
        offset_x = rect.left() + (rect.width() - span_x * scale) / 2.0
        offset_y = rect.top() + (rect.height() - span_z * scale) / 2.0

        return QPointF(offset_x + (x - min_x) * scale, offset_y + (z - min_z) * scale)

    # ---------- pintura ----------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette

        painter.fillRect(self.rect(), QColor(palette.surface))
        rect = self._plot_rect()

        if self.is_empty or self._bounds is None:
            painter.setPen(QPen(QColor(palette.text_muted), 1))
            painter.drawText(
                rect, int(Qt.AlignmentFlag.AlignCenter), "sem traçado disponível"
            )
            painter.end()
            return

        for path in self._paths:
            if path.is_empty:
                continue
            if path.has_categorical:
                self._paint_categorical(painter, path, rect)
            elif path.has_heatmap and self._heatmap_range is not None:
                self._paint_heatmap(painter, path, rect)
            else:
                self._paint_plain(painter, path, rect)

        # Ordem deliberada: marcas, cursor, e só então os rótulos. Texto é a
        # última coisa pintada para que nada o cubra — sem isso o anel do cursor
        # apaga o rótulo da curva sobre a qual ele está parado, que é
        # exatamente o momento em que se quer ler os dois.
        self._paint_markers(painter, rect)
        self._paint_cursor(painter, rect)
        self._paint_marker_labels(painter, rect)
        self._paint_legend(painter, rect)
        painter.end()

    def _paint_plain(self, painter: QPainter, path: TrackPath, rect: QRectF) -> None:
        pen = QPen(QColor(path.color), path.width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if path.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)

        # Reamostragem proporcional ao tamanho na tela — ver `_segment_budget`.
        step = max(1, len(path.points) // self._segment_budget(per_pixel=1.5))
        projected = [self._project(x, z, rect) for x, z in path.points[::step]]
        if len(projected) > 1:
            painter.drawPolyline(projected)

    def _paint_heatmap(self, painter: QPainter, path: TrackPath, rect: QRectF) -> None:
        """Pinta o traçado em segmentos coloridos pela magnitude."""
        assert self._heatmap_range is not None
        low, high = self._heatmap_range
        span = high - low or 1.0
        ramp = self._theme.palette.speed_ramp

        total = len(path.points)
        step = max(1, total // max(HEATMAP_SEGMENTS, self._segment_budget(per_pixel=0.4)))

        pen = QPen()
        pen.setWidthF(path.width + 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for start in range(0, total - step, step):
            end = min(start + step, total - 1)
            # A cor do segmento vem da média do trecho, não de uma ponta: usar a
            # ponta faria a cor saltar meio passo a cada reamostragem.
            middle = (path.values[start] + path.values[end]) / 2.0
            pen.setColor(QColor(ramp.at((middle - low) / span)))
            painter.setPen(pen)
            painter.drawLine(
                self._project(*path.points[start], rect),
                self._project(*path.points[end], rect),
            )

    def _paint_categorical(
        self, painter: QPainter, path: TrackPath, rect: QRectF
    ) -> None:
        """Pinta o traçado com a cor pronta de cada trecho.

        A reamostragem é mais fina que a do mapa de calor de propósito. Numa
        rampa, perder um trecho curto muda o tom de um segmento e ninguém nota;
        aqui um toque de freio de 40 m é um **evento**, e se ele cair entre duas
        amostras some inteiro — o mapa passa a afirmar que o piloto atravessou a
        curva sem tocar no freio.
        """
        total = len(path.points)
        step = max(1, total // self._segment_budget(per_pixel=1.0))

        pen = QPen()
        pen.setWidthF(path.width + 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for start in range(0, total - step, step):
            end = min(start + step, total - 1)
            # A cor do meio do trecho, e não uma média: são estados, e a média
            # de "freando" com "acelerando" não é um estado que existiu.
            pen.setColor(QColor(path.colors[(start + end) // 2]))
            painter.setPen(pen)
            painter.drawLine(
                self._project(*path.points[start], rect),
                self._project(*path.points[end], rect),
            )

    def _paint_markers(self, painter: QPainter, rect: QRectF) -> None:
        palette = self._theme.palette
        for marker in self._markers:
            center = self._project(marker.x, marker.z, rect)
            painter.setPen(QPen(QColor(marker.color), 1.5))
            # O preenchimento de superfície separa o marcador do traçado por
            # baixo; sem ele, marcador e linha da mesma cor viram um borrão.
            painter.setBrush(
                QColor(palette.surface) if marker.hollow else QColor(marker.color)
            )
            painter.drawEllipse(center, marker.radius, marker.radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_marker_labels(self, painter: QPainter, rect: QRectF) -> None:
        palette = self._theme.palette
        font = QFont(self._theme.type_scale.family_ui.split(",")[0].strip("'"))
        font.setPixelSize(self._theme.type_scale.micro)
        painter.setFont(font)
        painter.setPen(QPen(QColor(palette.text_secondary), 1))

        for marker in self._markers:
            if not marker.label:
                continue
            center = self._project(marker.x, marker.z, rect)
            # Afasta o rótulo o suficiente para caber o anel do cursor, que é
            # maior que o marcador.
            painter.drawText(
                QPointF(center.x() + marker.radius + 7, center.y() + 4),
                marker.label,
            )

    def _paint_cursor(self, painter: QPainter, rect: QRectF) -> None:
        """Marca no traçado a posição que os gráficos estão lendo."""
        if self._cursor_m is None:
            return
        palette = self._theme.palette

        for path in self._paths:
            index = path.index_at_distance(self._cursor_m)
            if index is None:
                continue
            center = self._project(*path.points[index], rect)
            # Anel de superfície por baixo, para o marcador não sumir sobre uma
            # linha da mesma claridade.
            painter.setPen(QPen(QColor(palette.surface), 3.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 5.0, 5.0)
            painter.setPen(QPen(QColor(palette.text_primary), 2.0))
            painter.drawEllipse(center, 5.0, 5.0)
            break  # só o primeiro traçado ganha cursor: dois confundiriam

    def _paint_legend(self, painter: QPainter, rect: QRectF) -> None:
        palette = self._theme.palette
        font = QFont(self._theme.type_scale.family_ui.split(",")[0].strip("'"))
        font.setPixelSize(self._theme.type_scale.micro)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        if self._legend:
            self._paint_discrete_legend(painter, rect, metrics)
            return

        if self._heatmap_range is not None:
            self._paint_heatmap_legend(painter, rect, metrics)
            return

        # Legenda de identidade, para traçados sobrepostos.
        legend_y = rect.bottom() - 4
        for path in reversed(self._paths):
            if path.is_empty:
                continue
            painter.setPen(QPen(QColor(path.color), 2))
            painter.drawLine(
                QPointF(rect.left(), legend_y - 4),
                QPointF(rect.left() + 14, legend_y - 4),
            )
            painter.setPen(QPen(QColor(palette.text_secondary), 1))
            painter.drawText(QPointF(rect.left() + 20, legend_y), path.label)
            legend_y -= 14

    def _paint_heatmap_legend(
        self, painter: QPainter, rect: QRectF, metrics: QFontMetrics
    ) -> None:
        """Barra de escala com os valores das duas pontas.

        Sem os números, a cor diz só "mais" e "menos"; com eles, diz quanto — e
        é a diferença entre um enfeite e uma leitura.
        """
        assert self._heatmap_range is not None
        low, high = self._heatmap_range
        palette = self._theme.palette
        ramp = palette.speed_ramp

        top = rect.bottom() + 8
        left = rect.left()

        for i in range(LEGEND_WIDTH):
            painter.setPen(QPen(QColor(ramp.at(i / (LEGEND_WIDTH - 1))), 1))
            painter.drawLine(
                QPointF(left + i, top), QPointF(left + i, top + LEGEND_HEIGHT)
            )

        painter.setPen(QPen(QColor(palette.text_muted), 1))
        baseline = top + LEGEND_HEIGHT + metrics.ascent() + 1
        painter.drawText(QPointF(left, baseline), f"{low:.0f}")
        high_text = f"{high:.0f}"
        painter.drawText(
            QPointF(left + LEGEND_WIDTH - metrics.horizontalAdvance(high_text), baseline),
            high_text,
        )
        if self._heatmap_label:
            painter.drawText(
                QPointF(left + LEGEND_WIDTH + 8, top + LEGEND_HEIGHT),
                self._heatmap_label,
            )

    # ---------- interação ----------

    def _paint_discrete_legend(
        self, painter: QPainter, rect: QRectF, metrics: QFontMetrics
    ) -> None:
        """Amostras de cor com rótulo, para os canais categóricos.

        A barra de gradiente responde "quanto"; aqui a pergunta é "qual", e uma
        barra contínua sugeriria estados intermediários que não existem.
        """
        palette = self._theme.palette
        x = rect.left()
        y = rect.bottom() + 6

        for color, label in self._legend:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, y, 10.0, float(LEGEND_HEIGHT)), 2.0, 2.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            painter.setPen(QPen(QColor(palette.text_muted), 1))
            painter.drawText(QPointF(x + 14, y + LEGEND_HEIGHT - 1), label)
            x += 14 + metrics.horizontalAdvance(label) + 14

    def _distance_at_pixel(self, position: QPointF) -> float | None:
        """Distância na volta do ponto de traçado mais próximo do ponteiro.

        Varre o primeiro traçado localizável. É O(n) por evento de mouse, o que
        num widget é irrelevante — e uma estrutura espacial aqui seria
        complexidade sem ganho mensurável.
        """
        if self._bounds is None:
            return None
        rect = self._plot_rect()

        for path in self._paths:
            if not path.is_locatable or path.is_empty:
                continue
            step = max(1, len(path.points) // self._segment_budget())
            best_index: int | None = None
            best_gap = float("inf")
            for index in range(0, len(path.points), step):
                pixel = self._project(*path.points[index], rect)
                gap = (pixel.x() - position.x()) ** 2 + (pixel.y() - position.y()) ** 2
                if gap < best_gap:
                    best_gap, best_index = gap, index
            # Longe demais do traçado não é uma leitura: 30 px de raio evita que
            # um clique no canto vazio salte o cursor para o outro lado da pista.
            if best_index is not None and best_gap <= 30**2:
                return path.distances[best_index]
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802  (API do Qt)
        distance = self._distance_at_pixel(event.position())
        if distance is not None:
            self.set_cursor(distance)
            self.hovered.emit(distance)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802  (API do Qt)
        distance = self._distance_at_pixel(event.position())
        if distance is not None:
            self.set_cursor(distance)
            # `clicked` além de `hovered`: quem escuta precisa distinguir
            # "o ponteiro passou por aqui" de "escolhi este ponto", que é o
            # que trava o cursor.
            self.clicked.emit(distance)
            self.hovered.emit(distance)
        super().mousePressEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802  (API do Qt)
        self.hover_left.emit()
        super().leaveEvent(event)  # type: ignore[arg-type]
