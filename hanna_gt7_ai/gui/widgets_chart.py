"""
Um gráfico compacto (velocidade, marcha, freio, etc.) que sabe desenhar uma
linha vertical de cursor numa posição de distância arbitrária, e que avisa
quando o mouse se move sobre ele — usado pela aba de Telemetria para
sincronizar vários gráficos empilhados pelo mesmo ponto da pista.

Suporta zoom (roda do mouse no eixo X), pan (arrastar com botão esquerdo),
reset (duplo-clique) e tooltip sobreposto com os valores de cada série no
ponto do cursor.
"""

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QWidget
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis

GRID_COLOR = QColor("#23262f")
AXIS_LABEL_COLOR = QColor("#8a8e99")
CROSSHAIR_COLOR = QColor("#ffffff")


class SyncedMiniChart(QChartView):
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
        self.setMinimumHeight(90)
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

        self._tooltip_bg = QGraphicsRectItem()
        self._tooltip_bg.setBrush(QBrush(QColor(26, 29, 37, 220)))
        self._tooltip_bg.setPen(QPen(QColor("#454a58"), 1))
        self._tooltip_bg.setZValue(1001)
        self.scene().addItem(self._tooltip_bg)
        self._tooltip_bg.hide()

        self._tooltip_text = QGraphicsSimpleTextItem()
        self._tooltip_text.setBrush(QColor("#e8e8ec"))
        tooltip_font = QFont()
        tooltip_font.setPixelSize(11)
        self._tooltip_text.setFont(tooltip_font)
        self._tooltip_text.setZValue(1002)
        self.scene().addItem(self._tooltip_text)
        self._tooltip_text.hide()

        self._sector_items = []
        self._last_sectors = []

        self._max_distance = 1.0
        self._full_x_min = 0.0
        self._full_x_max = 1.0
        self._series_data: list[tuple[str, str, list]] = []

        self._drag_start_pos = None
        self._drag_start_range = None

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
        self._series_data = list(series_defs)

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
        self._full_x_min = 0.0
        self._full_x_max = max_distance
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
        self._show_tooltip(distance_m, top)

    def hide_crosshair(self):
        self._crosshair.hide()
        self._tooltip_bg.hide()
        self._tooltip_text.hide()

    def _show_tooltip(self, distance_m: float, anchor: QPointF):
        if not self._series_data:
            self._tooltip_bg.hide()
            self._tooltip_text.hide()
            return

        import bisect
        lines = [f"{distance_m:.0f}m"]
        for name, color, points in self._series_data:
            if not points:
                continue
            distances = [p[0] for p in points]
            idx = bisect.bisect_left(distances, distance_m)
            if idx == 0:
                val = points[0][1]
            elif idx >= len(points):
                val = points[-1][1]
            else:
                d0, v0 = points[idx - 1]
                d1, v1 = points[idx]
                ratio = (distance_m - d0) / (d1 - d0) if d1 != d0 else 0
                val = v0 + ratio * (v1 - v0)
            label = name if len(self._series_data) > 1 else ""
            if label:
                lines.append(f"{label}: {val:.1f}")
            else:
                lines.append(f"{val:.1f}")

        text = "\n".join(lines)
        self._tooltip_text.setText(text)
        self._tooltip_text.show()

        tr = self._tooltip_text.boundingRect()
        pad = 6
        bg_w = tr.width() + pad * 2
        bg_h = tr.height() + pad * 2

        tx = anchor.x() + 10
        ty = anchor.y() + 4
        scene_rect = self.sceneRect()
        if tx + bg_w > scene_rect.right() - 4:
            tx = anchor.x() - bg_w - 10

        self._tooltip_bg.setRect(QRectF(tx, ty, bg_w, bg_h))
        self._tooltip_text.setPos(tx + pad, ty + pad)
        self._tooltip_bg.show()

    # --- zoom / pan ---

    def wheelEvent(self, event):
        if not self.chart().series():
            super().wheelEvent(event)
            return

        pos = event.position() if hasattr(event, "position") else event.pos()
        series = self.chart().series()[0]
        val = self.chart().mapToValue(pos, series)
        center_x = val.x()

        cur_min = self._axis_x.min()
        cur_max = self._axis_x.max()
        span = cur_max - cur_min

        delta = event.angleDelta().y()
        factor = 0.85 if delta > 0 else 1.0 / 0.85
        new_span = span * factor

        full_span = self._full_x_max - self._full_x_min
        new_span = max(full_span * 0.02, min(full_span, new_span))

        ratio = (center_x - cur_min) / span if span > 0 else 0.5
        new_min = center_x - new_span * ratio
        new_max = center_x + new_span * (1 - ratio)

        new_min = max(self._full_x_min, new_min)
        new_max = min(self._full_x_max, new_max)

        self._axis_x.setRange(new_min, new_max)
        if self._last_sectors:
            self.set_sector_lines(self._last_sectors)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.chart().series():
            self._drag_start_pos = event.position() if hasattr(event, "position") else event.pos()
            self._drag_start_range = (self._axis_x.min(), self._axis_x.max())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()

        if self._drag_start_pos is not None and self.chart().series():
            series = self.chart().series()[0]
            start_val = self.chart().mapToValue(self._drag_start_pos, series).x()
            cur_val = self.chart().mapToValue(pos, series).x()
            dx = start_val - cur_val

            old_min, old_max = self._drag_start_range
            new_min = old_min + dx
            new_max = old_max + dx

            span = new_max - new_min
            if new_min < self._full_x_min:
                new_min = self._full_x_min
                new_max = new_min + span
            if new_max > self._full_x_max:
                new_max = self._full_x_max
                new_min = new_max - span

            self._axis_x.setRange(new_min, new_max)
            if self._last_sectors:
                self.set_sector_lines(self._last_sectors)
            event.accept()
            return

        if self.chart().series():
            series = self.chart().series()[0]
            value = self.chart().mapToValue(pos, series)
            distance = max(0.0, min(self._max_distance, value.x()))
            self.hovered_at_distance.emit(distance)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start_pos is not None:
            self._drag_start_pos = None
            self._drag_start_range = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._axis_x.setRange(self._full_x_min, self._full_x_max)
        if self._last_sectors:
            self.set_sector_lines(self._last_sectors)
        event.accept()

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
        self.setMinimumHeight(60)
        self.setMaximumHeight(height + 40)
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


class LiveDualStripChart(QChartView):
    """Gráfico rolante com duas séries sobrepostas (acelerador + freio)."""

    def __init__(self, title: str, color_a: str, color_b: str,
                 max_points: int = 150, y_range=(0, 100), height: int = 120):
        chart = QChart()
        chart.legend().hide()
        chart.setTitle(title)
        chart.setTitleBrush(QColor("#c8cad0"))
        chart.setBackgroundBrush(QColor("#1a1d25"))
        chart.setMargins(chart.margins().__class__(6, 2, 6, 2))

        super().__init__(chart)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(70)
        self.setMaximumHeight(height + 40)
        self.setStyleSheet("background: transparent; border: none;")

        self._series_a = QLineSeries()
        pen_a = QPen(QColor(color_a))
        pen_a.setWidth(2)
        self._series_a.setPen(pen_a)

        self._series_b = QLineSeries()
        pen_b = QPen(QColor(color_b))
        pen_b.setWidth(2)
        self._series_b.setPen(pen_b)

        self.chart().addSeries(self._series_a)
        self.chart().addSeries(self._series_b)

        self._axis_x = QValueAxis()
        self._axis_x.setLabelsVisible(False)
        self._axis_x.setGridLineColor(GRID_COLOR)

        self._axis_y = QValueAxis()
        self._axis_y.setRange(*y_range)
        self._axis_y.setLabelsColor(QColor("#c8cad0"))
        self._axis_y.setGridLineColor(GRID_COLOR)
        self._axis_y.setTickCount(3)

        self.chart().addAxis(self._axis_x, Qt.AlignBottom)
        self.chart().addAxis(self._axis_y, Qt.AlignLeft)
        self._series_a.attachAxis(self._axis_x)
        self._series_a.attachAxis(self._axis_y)
        self._series_b.attachAxis(self._axis_x)
        self._series_b.attachAxis(self._axis_y)

        self._max_points = max_points
        self._values_a = []
        self._values_b = []

    def push(self, value_a: float, value_b: float):
        self._values_a.append(value_a)
        self._values_b.append(value_b)
        if len(self._values_a) > self._max_points:
            self._values_a.pop(0)
        if len(self._values_b) > self._max_points:
            self._values_b.pop(0)

        self._series_a.clear()
        self._series_b.clear()
        for i, v in enumerate(self._values_a):
            self._series_a.append(i, v)
        for i, v in enumerate(self._values_b):
            self._series_b.append(i, v)
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

    def __init__(self, title: str = "Traçado (posição X-Z)", height: int = 220, show_controls: bool = True):
        super().__init__()
        self._title = title
        self.setMinimumHeight(height)
        self.setStyleSheet("background: transparent;")
        self._paths: list[tuple[str, str, list]] = []
        self._markers: list[tuple[float, float, str]] = []
        self._min_x = self._max_x = self._min_z = self._max_z = None
        self._zoom_level = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_z = 0.0
        self._drag_start = None
        self._drag_pan_start = (0.0, 0.0)
        self._show_controls = show_controls

    def clear(self):
        self._paths = []
        self._markers = []
        self._min_x = self._max_x = self._min_z = self._max_z = None
        self._zoom_level = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_z = 0.0
        self.update()

    def zoom_in(self):
        self._zoom_level = min(self._zoom_level * 1.3, 10.0)
        self.update()

    def zoom_out(self):
        self._zoom_level = max(self._zoom_level / 1.3, 0.5)
        self.update()

    def zoom_reset(self):
        self._zoom_level = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_z = 0.0
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom_level = min(self._zoom_level * 1.15, 10.0)
        elif delta < 0:
            self._zoom_level = max(self._zoom_level / 1.15, 0.5)
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._show_controls:
            pos = event.position() if hasattr(event, 'position') else event.pos()
            action = self._hit_zoom_button(pos)
            if action == "in":
                self.zoom_in()
                event.accept()
                return
            elif action == "out":
                self.zoom_out()
                event.accept()
                return
            elif action == "reset":
                self.zoom_reset()
                event.accept()
                return
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier):
            self._drag_start = event.position() if hasattr(event, 'position') else event.pos()
            self._drag_pan_start = (self._pan_offset_x, self._pan_offset_z)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            pos = event.position() if hasattr(event, 'position') else event.pos()
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            self._pan_offset_x = self._drag_pan_start[0] + dx
            self._pan_offset_z = self._drag_pan_start[1] + dy
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start is not None:
            self._drag_start = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

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
        base_scale = min(rect.width() / (span_x * padding), rect.height() / (span_z * padding))
        scale = base_scale * self._zoom_level
        center_x = (self._min_x + self._max_x) / 2
        center_z = (self._min_z + self._max_z) / 2
        wx = rect.center().x() + (x - center_x) * scale + self._pan_offset_x
        wy = rect.center().y() + (z - center_z) * scale + self._pan_offset_z
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
            self._draw_zoom_controls(painter)
            painter.end()
            return

        painter.setClipRect(plot_rect)

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

        painter.setClipping(False)
        self._draw_zoom_controls(painter)
        painter.end()

    def _draw_zoom_controls(self, painter: QPainter):
        if not self._show_controls:
            return
        btn_size = 22
        margin = 8
        x = self.rect().right() - margin - btn_size
        y = self.rect().top() + margin

        for i, label in enumerate(["+", "-", "R"]):
            by = y + i * (btn_size + 4)
            rect = self.rect().__class__(x, by, btn_size, btn_size)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#2a2e3a")))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#c8cad0"))
            from PySide6.QtGui import QFont
            font = QFont()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, label)

        if self._zoom_level != 1.0:
            painter.setPen(QColor("#6b6f7a"))
            font = QFont()
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(x - 20, y + 3 * (btn_size + 4), f"{self._zoom_level:.1f}x")

    def mouseDoubleClickEvent(self, event):
        self.zoom_reset()
        event.accept()

    def _hit_zoom_button(self, pos) -> str | None:
        btn_size = 22
        margin = 8
        x = self.rect().right() - margin - btn_size
        y = self.rect().top() + margin
        for i, action in enumerate(["in", "out", "reset"]):
            by = y + i * (btn_size + 4)
            if x <= pos.x() <= x + btn_size and by <= pos.y() <= by + btn_size:
                return action
        return None
