"""
Um gráfico compacto (velocidade, marcha, freio, etc.) que sabe desenhar uma
linha vertical de cursor numa posição de distância arbitrária, e que avisa
quando o mouse se move sobre ele — usado pela aba de Comparação para
sincronizar vários gráficos empilhados pelo mesmo ponto da pista.
"""

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QGraphicsLineItem
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
