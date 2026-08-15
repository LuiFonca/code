"""
Mapa de pista — o traçado desenhado a partir das coordenadas X/Z.

O GT7 não transmite um mapa; transmite a posição do carro. O traçado é o
rastro dessa posição ao longo de uma volta, e é isso que se desenha aqui.

O widget aceita **vários traçados sobrepostos**, que é o que dá sentido à
comparação: duas voltas no mesmo mapa mostram onde as linhas divergem. Aceita
também marcadores — usados para plotar os ápices detectados na Fase 4 e o
ponto onde mais se perdeu tempo.

O enquadramento preserva a proporção. Esticar o traçado para preencher o widget
deformaria a geometria da pista, e uma curva de raio constante pareceria oval —
o que confunde exatamente quem está tentando ler a linha.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design.tokens import Theme

PADDING = 18


@dataclass(slots=True)
class TrackPath:
    """Um traçado: pontos (x, z) e como desenhá-lo."""

    label: str
    color: str
    points: list[tuple[float, float]] = field(default_factory=list)
    width: float = 2.0
    dashed: bool = False

    @property
    def is_empty(self) -> bool:
        return len(self.points) < 2


@dataclass(slots=True)
class TrackMarker:
    """Um ponto de interesse sobre o traçado."""

    x: float
    z: float
    color: str
    label: str = ""
    radius: float = 4.0


class TrackMap(QWidget):
    """Desenha um ou mais traçados com proporção preservada."""

    def __init__(self, theme: Theme, *, height: int = 260) -> None:
        super().__init__()
        self._theme = theme
        self._paths: list[TrackPath] = []
        self._markers: list[TrackMarker] = []
        self._bounds: tuple[float, float, float, float] | None = None

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_paths(self, paths: list[TrackPath]) -> None:
        self._paths = paths
        self._recompute_bounds()
        self.update()

    def set_markers(self, markers: list[TrackMarker]) -> None:
        self._markers = markers
        self.update()

    def clear(self) -> None:
        self._paths = []
        self._markers = []
        self._bounds = None
        self.update()

    @property
    def is_empty(self) -> bool:
        return not self._paths or all(p.is_empty for p in self._paths)

    def _recompute_bounds(self) -> None:
        xs = [x for path in self._paths for x, _ in path.points]
        zs = [z for path in self._paths for _, z in path.points]
        if not xs or not zs:
            self._bounds = None
            return
        self._bounds = (min(xs), min(zs), max(xs), max(zs))

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

        return QPointF(
            offset_x + (x - min_x) * scale,
            offset_y + (z - min_z) * scale,
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (API do Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme.palette

        painter.fillRect(self.rect(), QColor(palette.surface))
        rect = QRectF(
            PADDING,
            PADDING,
            max(1.0, self.width() - 2 * PADDING),
            max(1.0, self.height() - 2 * PADDING),
        )

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
            pen = QPen(QColor(path.color), path.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if path.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)

            # Reamostragem pelo mesmo motivo do gráfico: 6000 pontos num widget
            # de 400 px repintam o mesmo pixel dezenas de vezes.
            step = max(1, len(path.points) // 1200)
            projected = [self._project(x, z, rect) for x, z in path.points[::step]]
            if len(projected) > 1:
                painter.drawPolyline(projected)

        font = QFont(self._theme.type_scale.family_ui.split(",")[0].strip("'"))
        font.setPixelSize(self._theme.type_scale.micro)
        painter.setFont(font)

        for marker in self._markers:
            center = self._project(marker.x, marker.z, rect)
            painter.setPen(QPen(QColor(marker.color), 1.5))
            painter.setBrush(QColor(marker.color))
            painter.drawEllipse(center, marker.radius, marker.radius)
            if marker.label:
                painter.setPen(QPen(QColor(palette.text_secondary), 1))
                painter.drawText(
                    QPointF(center.x() + marker.radius + 3, center.y() + 4),
                    marker.label,
                )
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Legenda, canto inferior esquerdo.
        legend_y = rect.bottom() - 4
        for path in reversed(self._paths):
            if path.is_empty:
                continue
            painter.setPen(QPen(QColor(path.color), 2))
            painter.drawLine(
                QPointF(rect.left(), legend_y - 4), QPointF(rect.left() + 14, legend_y - 4)
            )
            painter.setPen(QPen(QColor(palette.text_secondary), 1))
            painter.drawText(QPointF(rect.left() + 20, legend_y), path.label)
            legend_y -= 14

        painter.end()
