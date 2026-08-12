"""
Aba "Ao Vivo" — dashboard de telemetria em tempo real com estética
de painel de carro de corrida: delta grande, velocidade/marcha centrais,
RPM com barra colorida, gráfico combinado de pedais, pneus com mapa
da pista ao centro e traçado com ghost da volta anterior.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar,
    QScrollArea,
)
from PySide6.QtCore import Qt

from gui.widgets import MetricCard, DeltaCard, format_ms
from gui.widgets_chart import LiveDualStripChart, TrackMapWidget
from gui.widgets_tire import TireTempPanel

TRAIL_COLOR = "#4f7cff"
GHOST_COLOR = "#2a3a5c"
MARKER_COLOR = "#3ddc84"

RPM_MAX_DEFAULT = 9000


class _RpmBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self._label = QLabel("RPM")
        self._label.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        self._value = QLabel("0")
        self._value.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        header.addWidget(self._label)
        header.addStretch()
        header.addWidget(self._value)
        layout.addLayout(header)

        self._bar = QProgressBar()
        self._bar.setRange(0, RPM_MAX_DEFAULT)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(16)
        self._set_bar_color("#3ddc84")
        layout.addWidget(self._bar)

        self._tracked_max = RPM_MAX_DEFAULT

    def set_rpm(self, rpm: float):
        rpm_int = int(rpm)
        if rpm_int > self._tracked_max:
            self._tracked_max = rpm_int + 500
            self._bar.setRange(0, self._tracked_max)
        self._bar.setValue(rpm_int)
        self._value.setText(f"{rpm_int:,}".replace(",", "."))

        ratio = rpm_int / self._tracked_max if self._tracked_max else 0
        if ratio > 0.85:
            self._set_bar_color("#ff5c5c")
        elif ratio > 0.7:
            self._set_bar_color("#f2c94c")
        else:
            self._set_bar_color("#3ddc84")

    def _set_bar_color(self, color: str):
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #23262f;
                border-radius: 8px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 8px;
            }}
        """)


class _BigValueCard(QFrame):
    def __init__(self, label: str, font_size: int = 52, color: str = "#ffffff"):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignCenter)

        self._value = QLabel("--")
        self._value.setAlignment(Qt.AlignCenter)
        self._value.setStyleSheet(
            f"color: {color}; font-size: {font_size}px; font-weight: 800;"
        )

        self._label = QLabel(label.upper())
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )

        layout.addWidget(self._value)
        layout.addWidget(self._label)

    def set_value(self, text):
        self._value.setText(str(text))


class _PedalBar(QFrame):
    def __init__(self, label: str, color: str):
        super().__init__()
        self._color = color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._label = QLabel(label.upper())
        self._label.setStyleSheet(
            "color: #c8cad0; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
        )
        self._label.setMinimumWidth(60)
        self._label.setMaximumWidth(100)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(18)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #23262f;
                border-radius: 9px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 9px;
            }}
        """)

        self._pct = QLabel("0%")
        self._pct.setStyleSheet("color: #e8e8ec; font-size: 14px; font-weight: 700;")
        self._pct.setFixedWidth(42)
        self._pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self._label)
        layout.addWidget(self._bar, stretch=1)
        layout.addWidget(self._pct)

    def set_value(self, percent: float):
        v = int(max(0, min(100, percent)))
        self._bar.setValue(v)
        self._pct.setText(f"{v}%")


class LiveDashboardTab(QWidget):
    def __init__(self):
        super().__init__()
        self._last_lap_count = None
        self._ghost_points = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 6, 4, 4)
        root.setSpacing(6)

        # --- Delta (best + previous) ---
        self.card_delta = DeltaCard("DELTA VS MELHOR VOLTA")
        self.card_delta_prev = DeltaCard("DELTA VS VOLTA ANTERIOR")
        delta_row = QHBoxLayout()
        delta_row.setSpacing(8)
        delta_row.addWidget(self.card_delta, stretch=1)
        delta_row.addWidget(self.card_delta_prev, stretch=1)
        root.addLayout(delta_row)

        # --- Velocidade + Marcha ---
        speed_gear_row = QHBoxLayout()
        speed_gear_row.setSpacing(8)

        self.card_speed = _BigValueCard("km/h", font_size=56)
        self.card_gear = _BigValueCard("marcha", font_size=52, color="#4f7cff")

        speed_gear_row.addWidget(self.card_speed, stretch=3)
        speed_gear_row.addWidget(self.card_gear, stretch=1)
        root.addLayout(speed_gear_row)

        # --- RPM ---
        self.rpm_bar = _RpmBar()
        root.addWidget(self.rpm_bar)

        # --- Info compacta: Volta, Tempo, Combustível ---
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.card_lap = MetricCard("Volta")
        self.card_laptime = MetricCard("Tempo de volta")
        self.card_fuel = MetricCard("Combustível", "%")
        info_row.addWidget(self.card_lap)
        info_row.addWidget(self.card_laptime)
        info_row.addWidget(self.card_fuel)
        root.addLayout(info_row)

        # --- Pedais (barras) ---
        pedals_frame = QFrame()
        pedals_frame.setObjectName("card")
        pedals_layout = QVBoxLayout(pedals_frame)
        pedals_layout.setContentsMargins(14, 6, 14, 6)
        pedals_layout.setSpacing(4)

        self.pedal_throttle = _PedalBar("Acelerador", "#3ddc84")
        self.pedal_brake = _PedalBar("Freio", "#ff5c5c")
        pedals_layout.addWidget(self.pedal_throttle)
        pedals_layout.addWidget(self.pedal_brake)
        root.addWidget(pedals_frame)

        # --- Gráfico combinado acelerador + freio ---
        self.chart_pedals = LiveDualStripChart(
            "Acelerador / Freio (%)", "#3ddc84", "#ff5c5c", height=110
        )
        root.addWidget(self.chart_pedals)

        # --- Pneus + Mapa da pista ---
        tire_header = QLabel("PNEUS  /  MAPA DA PISTA")
        tire_header.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        root.addWidget(tire_header)

        self.tire_panel = TireTempPanel()
        self.track_map = TrackMapWidget("Traçado", height=180)
        map_layout = QVBoxLayout(self.tire_panel.map_slot)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(self.track_map)
        root.addWidget(self.tire_panel)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # --- API pública ---

    def render_frame(self, frame):
        self.card_speed.set_value(f"{frame.speed_kmh:.0f}")
        self.rpm_bar.set_rpm(frame.rpm)
        self.card_gear.set_value("N" if frame.gear == 0 else frame.gear)
        self.card_lap.set_value(f"{frame.lap_count}/{frame.total_laps}")
        self.card_laptime.set_value(format_ms(frame.current_lap_ms))
        self.card_fuel.set_value(f"{frame.fuel:.0f}")

        self.pedal_throttle.set_value(frame.throttle)
        self.pedal_brake.set_value(frame.brake)
        self.chart_pedals.push(frame.throttle, frame.brake)

        self.tire_panel.set_temps(
            frame.tire_temp_fl, frame.tire_temp_fr,
            frame.tire_temp_rl, frame.tire_temp_rr,
        )

        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._save_ghost()
            self.track_map.clear()
            self._draw_ghost()

        self._last_lap_count = frame.lap_count
        self.track_map.append_point("atual", TRAIL_COLOR, frame.position_x, frame.position_z)
        self.track_map.set_marker(frame.position_x, frame.position_z, MARKER_COLOR)

    def render_delta(self, delta_seconds):
        self.card_delta.set_delta(delta_seconds)

    def render_delta_previous(self, delta_seconds):
        self.card_delta_prev.set_delta(delta_seconds)

    def render_stale(self):
        self.card_speed.set_value("0")
        self.rpm_bar.set_rpm(0)
        self.card_gear.set_value("N")
        self.card_lap.set_value("--/--")
        self.card_laptime.set_value(format_ms(None))
        self.card_fuel.set_value("0")
        self.pedal_throttle.set_value(0)
        self.pedal_brake.set_value(0)
        self.card_delta_prev.set_delta(None)

    def _save_ghost(self):
        for name, color, points in self.track_map._paths:
            if name == "atual" and len(points) > 10:
                self._ghost_points = list(points)
                return

    def _draw_ghost(self):
        if self._ghost_points:
            for x, z in self._ghost_points:
                self.track_map.append_point("ghost", GHOST_COLOR, x, z)
