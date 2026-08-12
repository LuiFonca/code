"""
Um gráfico compacto (velocidade, marcha, freio, etc.) que sabe desenhar uma
linha vertical de cursor numa posição de distância arbitrária, e que avisa
quando o mouse se move sobre ele — usado pela aba de Comparação para
sincronizar vários gráficos empilhados pelo mesmo ponto da pista.
"""

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import QGraphicsLineItem, QWidget
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis

GRID_COLOR = QColor("#23262f")
AXIS_LABEL_COLOR = QColor("#8a8e99")
CROSSHAIR_COLOR = QColor("#ffffff")


class SyncedMiniChart(QChartView):
    # Emitido quando o mouse se move sobre este gráfico, com a distância
    # (em metros) correspondente à posição horizontal do cursor.
    hovered_at_distance = Signal(float)
    hover_left = Signal()

    def __init__(self, title: str, height: int = 130):
        chart = QChart()
        chart.legend().hide()
        chart.setTitle(title)
        chart.setTitleBrush(AXIS_LABEL_COLOR)
        chart.setBackgroundBrush(QColor("#1a1d25"))
        chart.setMargins(chart.margins().__class__(6, 4, 6, 4))
        chart.setContentsMargins(0, 0, 0, 0)

        super().__init__(chart)
        self.setRenderHint(QPainter.Antialiasing)
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent; border: none;")

        self._axis_x = QValueAxis()
        self._axis_x.setLabelsColor(AXIS_LABEL_COLOR)
        self._axis_x.setGridLineColor(GRID_COLOR)
        self._axis_x.setLabelsVisible(False)

        self._axis_y = QValueAxis()
        self._axis_y.setLabelsColor(AXIS_LABEL_COLOR)
        self._axis_y.setGridLineColor(GRID_COLOR)

        self.chart().addAxis(self._axis_x, Qt.AlignBottom)
        self.chart().addAxis(self._axis_y, Qt.AlignLeft)

        self._crosshair = QGraphicsLineItem()
        pen = QPen(CROSSHAIR_COLOR)
        pen.setWidth(1)
        pen.setStyle(Qt.DashLine)
        self._crosshair.setPen(pen)
        self._crosshair.setZValue(1000)
        self.scene().addItem(self._crosshair)
        self._crosshair.hide()

        self._sector_items = []
        self._last_sectors = []

        self._max_distance = 1.0

    def set_sector_lines(self, sectors):
        """sectors: lista de (distance_m, label) — desenha linhas tracejadas
        verticais fixas (diferente do crosshair, que segue o mouse), marcando
        onde cada setor começa. Precisa ser chamado DEPOIS de set_series."""
        self._last_sectors = sectors
        for item in self._sector_items:
            self.scene().removeItem(item)
        self._sector_items = []

        if not self.chart().series() or not sectors:
            return

        series = self.chart().series()[0]
        y_min, y_max = self._axis_y.min(), self._axis_y.max()

        for distance_m, label in sectors:
            top = self.chart().mapToPosition(QPointF(distance_m, y_max), series)
            bottom = self.chart().mapToPosition(QPointF(distance_m, y_min), series)
            line = QGraphicsLineItem(top.x(), top.y(), bottom.x(), bottom.y())
            pen = QPen(QColor("#454a58"))
            pen.setStyle(Qt.DashLine)
            pen.setWidth(1)
            line.setPen(pen)
            line.setZValue(5)
            self.scene().addItem(line)
            self._sector_items.append(line)

            if label:
                text = self.scene().addSimpleText(label)
                text.setBrush(QColor("#6b6f7a"))
                text.setPos(top.x() + 3, top.y() + 2)
                text.setZValue(5)
                self._sector_items.append(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reposiciona as linhas de setor (a geometria do gráfico muda com
        # o tamanho da janela). O crosshair não precisa disso pois só
        # aparece durante o movimento do mouse, sempre recalculado na hora.
        if self._last_sectors:
            self.set_sector_lines(self._last_sectors)
        self.hide_crosshair()

    def set_series(self, series_defs, y_range=None):
        """series_defs: lista de (nome, cor, pontos[(distance_m, valor)])."""
        self.chart().removeAllSeries()

        max_distance = 1.0
        y_min, y_max = None, None

        for name, color, points in series_defs:
            if not points:
                continue
            series = QLineSeries()
            series.setName(name)
            pen = QPen(QColor(color))
            pen.setWidth(2)
            series.setPen(pen)
            for x, y in points:
                series.append(x, y)
                max_distance = max(max_distance, x)
                y_min = y if y_min is None else min(y_min, y)
                y_max = y if y_max is None else max(y_max, y)

            self.chart().addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_y)

        self._max_distance = max_distance
        self._axis_x.setRange(0, max_distance)

        if y_range:
            self._axis_y.setRange(*y_range)
        elif y_min is not None:
            padding = (y_max - y_min) * 0.1 or 1
            self._axis_y.setRange(y_min - padding, y_max + padding)

    def show_crosshair(self, distance_m: float):
        if not self.chart().series():
            return
        series = self.chart().series()[0]
        y_min, y_max = self._axis_y.min(), self._axis_y.max()
        top = self.chart().mapToPosition(QPointF(distance_m, y_max), series)
        bottom = self.chart().mapToPosition(QPointF(distance_m, y_min), series)
        self._crosshair.setLine(top.x(), top.y(), bottom.x(), bottom.y())
        self._crosshair.show()

    def hide_crosshair(self):
        self._crosshair.hide()

    def mouseMoveEvent(self, event):
        if self.chart().series():
            series = self.chart().series()[0]
            pos = event.position() if hasattr(event, "position") else event.pos()
            value = self.chart().mapToValue(pos, series)
            distance = max(0.0, min(self._max_distance, value.x()))
            self.hovered_at_distance.emit(distance)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_left.emit()
        super().leaveEvent(event)


class LiveStripChart(QChartView):
    """Gráfico rolante que mostra os últimos N valores recebidos — usado
    na aba Ao Vivo para acelerador e freio ao longo do tempo (não da
    distância, já que aqui o interesse é ver a evolução recente)."""

    def __init__(self, title: str, color: str, max_points: int = 150, y_range=(0, 100), height: int = 90):
        chart = QChart()
        chart.legend().hide()
        chart.setTitle(title)
        chart.setTitleBrush(AXIS_LABEL_COLOR)
        chart.setBackgroundBrush(QColor("#1a1d25"))

        super().__init__(chart)
        self.setRenderHint(QPainter.Antialiasing)
        self.setFixedHeight(height)
        self.setStyleSheet("background: transparent; border: none;")

        self._series = QLineSeries()
        pen = QPen(QColor(color))
        pen.setWidth(2)
        self._series.setPen(pen)
        self.chart().addSeries(self._series)

        self._axis_x = QValueAxis()
        self._axis_x.setLabelsVisible(False)
        self._axis_x.setGridLineColor(GRID_COLOR)

        self._axis_y = QValueAxis()
        self._axis_y.setRange(*y_range)
        self._axis_y.setLabelsColor(AXIS_LABEL_COLOR)
        self._axis_y.setGridLineColor(GRID_COLOR)

        self.chart().addAxis(self._axis_x, Qt.AlignBottom)
        self.chart().addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

        self._max_points = max_points
        self._values = []

    def push(self, value: float):
        self._values.append(value)
        if len(self._values) > self._max_points:
            self._values.pop(0)

        self._series.clear()
        for i, v in enumerate(self._values):
            self._series.append(i, v)
        self._axis_x.setRange(0, max(self._max_points - 1, 1))


class TrackMapWidget(QWidget):
    """Traçado da pista visto de cima (plano X-Z), desenhado a partir da
    posição de mundo real que o GT7 já envia em todo pacote de telemetria
    (position_x/position_z — ver telemetry/gt7_protocol.py). O GT7 não
    fornece um mapa oficial do circuito nem os pontos de curva, então isto
    é a trajetória real percorrida, não um mapa da pista em si; é o dado
    mais próximo de um "mapa" que o protocolo realmente oferece.

    Usado em dois modos:
    - Ao vivo: uma trilha que cresce com append_point(), com a posição
      atual marcada.
    - Comparação: dois traçados estáticos (volta A/B) via set_paths(),
      com um marcador sincronizado ao hover dos outros gráficos.
    """

    def __init__(self, title: str = "Traçado (posição X-Z)", height: int = 220):
        super().__init__()
        self._title = title
        self.setMinimumHeight(height)
        self.setStyleSheet("background: transparent;")
        self._paths: list[tuple[str, str, list]] = []
        self._markers: list[tuple[float, float, str]] = []
        self._min_x = self._max_x = self._min_z = self._max_z = None

    def clear(self):
        self._paths = []
        self._markers = []
        self._min_x = self._max_x = self._min_z = self._max_z = None
        self.update()

    def set_paths(self, paths):
        """paths: lista de (nome, cor_hex, pontos[(x, z)]). Substitui
        qualquer trilha/marcador existente e recalcula os limites do
        traçado a partir do zero."""
        self._paths = [(name, color, list(points)) for name, color, points in paths]
        self._markers = []
        self._recompute_bounds()
        self.update()

    def append_point(self, name: str, color: str, x: float, z: float, max_points: int = 8000):
        """Modo 'ao vivo': acrescenta um ponto à trilha `name` (cria se
        ainda não existir). Descarta pontos antigos além de max_points
        para uma sessão longa não crescer sem limite."""
        for i, (existing_name, existing_color, points) in enumerate(self._paths):
            if existing_name == name:
                points.append((x, z))
                if len(points) > max_points:
                    del points[: len(points) - max_points]
                break
        else:
            self._paths.append((name, color, [(x, z)]))
        self._grow_bounds(x, z)
        self.update()

    def set_marker(self, x, z, color: str = "#ffffff"):
        if x is None or z is None:
            self._markers = []
        else:
            self._markers = [(x, z, color)]
        self.update()

    def set_markers(self, markers):
        """markers: lista de (x, z, cor_hex) — usado quando mais de um
        ponto precisa aparecer ao mesmo tempo (ex: posição da Volta A e da
        Volta B no mesmo instante de distância, na aba de Comparação)."""
        self._markers = [m for m in markers if m[0] is not None and m[1] is not None]
        self.update()

    def clear_markers(self):
        self._markers = []
        self.update()

    def _grow_bounds(self, x: float, z: float):
        self._min_x = x if self._min_x is None else min(self._min_x, x)
        self._max_x = x if self._max_x is None else max(self._max_x, x)
        self._min_z = z if self._min_z is None else min(self._min_z, z)
        self._max_z = z if self._max_z is None else max(self._max_z, z)

    def _recompute_bounds(self):
        self._min_x = self._max_x = self._min_z = self._max_z = None
        for _, _, points in self._paths:
            for x, z in points:
                self._grow_bounds(x, z)

    def _to_widget_xy(self, x: float, z: float, rect):
        if self._min_x is None:
            return None
        span_x = max(self._max_x - self._min_x, 1.0)
        span_z = max(self._max_z - self._min_z, 1.0)
        padding = 1.15
        scale = min(rect.width() / (span_x * padding), rect.height() / (span_z * padding))
        center_x = (self._min_x + self._max_x) / 2
        center_z = (self._min_z + self._max_z) / 2
        wx = rect.center().x() + (x - center_x) * scale
        wy = rect.center().y() + (z - center_z) * scale
        return wx, wy

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1a1d25"))

        painter.setPen(AXIS_LABEL_COLOR)
        painter.drawText(self.rect().adjusted(8, 4, -8, 0), Qt.AlignLeft | Qt.AlignTop, self._title)

        plot_rect = self.rect().adjusted(16, 26, -16, -14)

        if self._min_x is None or plot_rect.width() <= 0 or plot_rect.height() <= 0:
            painter.setPen(QColor("#454a58"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Sem dados de posição para esta volta.")
            painter.end()
            return

        for name, color, points in self._paths:
            if len(points) < 2:
                continue
            pen = QPen(QColor(color))
            pen.setWidth(2)
            painter.setPen(pen)
            previous = None
            for x, z in points:
                current = self._to_widget_xy(x, z, plot_rect)
                if previous is not None:
                    painter.drawLine(QPointF(*previous), QPointF(*current))
                previous = current

        for x, z, color in self._markers:
            point = self._to_widget_xy(x, z, plot_rect)
            if point is None:
                continue
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.setBrush(QBrush(QColor(color)))
            painter.drawEllipse(QPointF(*point), 5, 5)

        painter.end()
