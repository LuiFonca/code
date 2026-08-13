"""
Aba "Ao Vivo" — painel de telemetria em tempo real.

View pura: recebe `LiveViewModel` e conecta sinais a widgets. Não tem timer
próprio nem lógica de stale — isso ficou no ViewModel, onde pode ser testado.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application.viewmodels.live_viewmodel import LiveViewModel
from ..widgets.widgets import DeltaCard, MetricCard, format_ms
from ..widgets.widgets_chart import LiveDualStripChart, TrackMapWidget
from ..widgets.widgets_tire import TireTempPanel

TRAIL_COLOR = "#4f7cff"
GHOST_COLOR = "#2a3a5c"
MARKER_COLOR = "#3ddc84"
RPM_MAX_DEFAULT = 9000


class _RpmBar(QFrame):
    """Barra de RPM que muda de cor conforme a faixa.

    Quando o jogo informa a faixa do shift light (`rpm_flashing_min/max`), usa
    esses valores; senão, calibra sozinha pelo maior RPM já visto — assim
    funciona em qualquer carro sem configuração.
    """

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        label = QLabel("RPM")
        label.setObjectName("sectionHeader")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        header.addWidget(label)
        header.addStretch()
        header.addWidget(self._value)
        layout.addLayout(header)

        self._bar = QProgressBar()
        self._bar.setRange(0, RPM_MAX_DEFAULT)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(16)
        self._set_bar_color("#3ddc84")
        layout.addWidget(self._bar)

        self._tracked_max = RPM_MAX_DEFAULT

    def set_rpm(self, rpm: float, flash_min: int = 0, flash_max: int = 0):
        rpm_int = int(rpm)
        if flash_max > 0:
            self._tracked_max = flash_max
            self._bar.setRange(0, flash_max)
        elif rpm_int > self._tracked_max:
            self._tracked_max = rpm_int + 500
            self._bar.setRange(0, self._tracked_max)
        self._bar.setValue(rpm_int)
        self._value.setText(f"{rpm_int:,}".replace(",", "."))

        if flash_min > 0 and flash_max > 0:
            if rpm_int >= flash_max:
                self._set_bar_color("#ff5c5c")
            elif rpm_int >= flash_min:
                self._set_bar_color("#f2c94c")
            else:
                self._set_bar_color("#3ddc84")
        else:
            ratio = rpm_int / self._tracked_max if self._tracked_max else 0
            if ratio > 0.85:
                self._set_bar_color("#ff5c5c")
            elif ratio > 0.7:
                self._set_bar_color("#f2c94c")
            else:
                self._set_bar_color("#3ddc84")

    def _set_bar_color(self, color: str):
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background-color: #23262f; border-radius: 8px; border: none; }}
            QProgressBar::chunk {{ background-color: {color}; border-radius: 8px; }}
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
        self._label.setObjectName("sectionHeader")

        layout.addWidget(self._value)
        layout.addWidget(self._label)

    def set_value(self, text):
        self._value.setText(str(text))


class _PedalBar(QFrame):
    def __init__(self, label: str, color: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._label = QLabel(label.upper())
        self._label.setObjectName("sectionHeader")
        self._label.setMinimumWidth(60)
        self._label.setMaximumWidth(100)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(18)
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background-color: #23262f; border-radius: 9px; border: none; }}
            QProgressBar::chunk {{ background-color: {color}; border-radius: 9px; }}
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


class _AidsRow(QFrame):
    """Estado das assistências e do corte de giro, aceso quando atuam.

    Os quatro indicadores vêm do bitfield de flags do pacote, que era
    decodificado e jogado fora. Ficam apagados até atuarem, para não competir
    com velocidade e delta na hora de olhar de relance.
    """

    LABELS = {
        "tcs": ("TCS", "#f2c94c"),
        "asm": ("ASM", "#4f7cff"),
        "limiter": ("CORTE", "#ff5c5c"),
        "handbrake": ("FREIO MÃO", "#ff9f4f"),
    }

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(8)

        title = QLabel("ASSISTÊNCIAS")
        title.setObjectName("sectionHeader")
        layout.addWidget(title)
        layout.addStretch()

        self._pills: dict[str, QLabel] = {}
        for key, (text, _color) in self.LABELS.items():
            pill = QLabel(text)
            pill.setAlignment(Qt.AlignCenter)
            pill.setMinimumWidth(64)
            self._pills[key] = pill
            layout.addWidget(pill)
        self.set_states({})

    def set_states(self, states: dict):
        for key, pill in self._pills.items():
            text, color = self.LABELS[key]
            if states.get(key):
                pill.setStyleSheet(
                    f"background-color: {color}; color: #12141a; font-size: 11px; "
                    "font-weight: 800; border-radius: 8px; padding: 3px 8px;"
                )
            else:
                pill.setStyleSheet(
                    "background-color: #23262f; color: #5a6070; font-size: 11px; "
                    "font-weight: 700; border-radius: 8px; padding: 3px 8px;"
                )


class LiveDashboardTab(QWidget):
    def __init__(self, view_model: LiveViewModel):
        super().__init__()
        self._vm = view_model
        self._last_lap_count: int | None = None
        self._ghost_points: list[tuple[float, float]] = []

        self._build_ui()

        self._vm.frame_updated.connect(self._on_frame)
        self._vm.delta_updated.connect(self._on_delta)
        self._vm.stale_entered.connect(self._on_stale)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 6, 4, 4)
        root.setSpacing(6)

        self.card_delta = DeltaCard("DELTA VS MELHOR VOLTA")
        self.card_delta_prev = DeltaCard("DELTA VS VOLTA ANTERIOR")
        delta_row = QHBoxLayout()
        delta_row.setSpacing(8)
        delta_row.addWidget(self.card_delta, stretch=1)
        delta_row.addWidget(self.card_delta_prev, stretch=1)
        root.addLayout(delta_row)

        self.card_speed = _BigValueCard("km/h", font_size=56)
        self.card_gear = _BigValueCard("marcha", font_size=52, color="#4f7cff")
        speed_row = QHBoxLayout()
        speed_row.setSpacing(8)
        speed_row.addWidget(self.card_speed, stretch=3)
        speed_row.addWidget(self.card_gear, stretch=1)
        root.addLayout(speed_row)

        self.rpm_bar = _RpmBar()
        root.addWidget(self.rpm_bar)

        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.card_lap = MetricCard("Volta")
        self.card_laptime = MetricCard("Tempo de volta")
        self.card_fuel = MetricCard("Combustível", "%")
        for c in (self.card_lap, self.card_laptime, self.card_fuel):
            info_row.addWidget(c)
        root.addLayout(info_row)

        # Assistências: já chegam em todo pacote e antes eram descartadas.
        # Saber que o controle de tração cortou potência numa saída de curva
        # muda a leitura do gráfico de acelerador.
        self.aids_row = _AidsRow()
        root.addWidget(self.aids_row)

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

        self.chart_pedals = LiveDualStripChart(
            "Acelerador / Freio (%)", "#3ddc84", "#ff5c5c", height=110
        )
        root.addWidget(self.chart_pedals)

        header = QLabel("PNEUS  /  MAPA DA PISTA")
        header.setObjectName("sectionHeader")
        root.addWidget(header)

        self.tire_panel = TireTempPanel()
        self.track_map = TrackMapWidget("Traçado", height=180)
        map_layout = QVBoxLayout(self.tire_panel.map_slot)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(self.track_map)
        root.addWidget(self.tire_panel)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ---------- reação ao ViewModel ----------

    def _on_frame(self, event):
        point, frame = event.point, event.frame

        self.card_speed.set_value(f"{point.speed_kmh:.0f}")
        self.card_gear.set_value("N" if point.gear == 0 else point.gear)
        self.pedal_throttle.set_value(point.throttle)
        self.pedal_brake.set_value(point.brake)
        self.chart_pedals.push(point.throttle, point.brake)
        self.tire_panel.set_temps(
            point.tire_temp_fl, point.tire_temp_fr,
            point.tire_temp_rl, point.tire_temp_rr,
        )

        # Campos que só existem no DTO de fio: número da volta, faixa do shift
        # light e capacidade do tanque não sobrevivem à normalização para
        # TelemetryPoint, mas o painel ao vivo precisa deles.
        if frame is not None:
            self.rpm_bar.set_rpm(
                point.rpm,
                getattr(frame, "rpm_flashing_min", 0),
                getattr(frame, "rpm_flashing_max", 0),
            )
            self.card_lap.set_value(f"{frame.lap_count}/{frame.total_laps}")
            self.card_laptime.set_value(format_ms(frame.current_lap_ms))

            capacity = getattr(frame, "fuel_capacity", 0)
            if capacity > 0:
                self.card_fuel.set_value(f"{(point.fuel_level / capacity) * 100:.1f}")
            else:
                self.card_fuel.set_value(f"{point.fuel_level:.1f}")

            self.aids_row.set_states({
                "tcs": getattr(frame, "tcs_active", False),
                "asm": getattr(frame, "asm_active", False),
                "limiter": getattr(frame, "rev_limiter_active", False),
                "handbrake": bool(getattr(frame, "flags", 0) & (1 << 6)),
            })

            if (
                self._last_lap_count is not None
                and frame.lap_count != self._last_lap_count
            ):
                self._save_ghost()
                self.track_map.clear()
                self._draw_ghost()
            self._last_lap_count = frame.lap_count
        else:
            self.rpm_bar.set_rpm(point.rpm)

        self.track_map.append_point(
            "atual", TRAIL_COLOR, point.position_x, point.position_z
        )
        self.track_map.set_marker(point.position_x, point.position_z, MARKER_COLOR)

    def _on_delta(self, delta_best, delta_previous):
        self.card_delta.set_delta(delta_best)
        self.card_delta_prev.set_delta(delta_previous)

    def _on_stale(self):
        """Zera a tela quando a transmissão para.

        Sem isto, o último valor recebido fica congelado e "carro parado" vira
        indistinguível de "perdi o sinal".
        """
        self.card_speed.set_value("0")
        self.rpm_bar.set_rpm(0)
        self.card_gear.set_value("N")
        self.card_lap.set_value("--/--")
        self.card_laptime.set_value(format_ms(None))
        self.card_fuel.set_value("0")
        self.pedal_throttle.set_value(0)
        self.pedal_brake.set_value(0)
        self.card_delta.set_delta(None)
        self.card_delta_prev.set_delta(None)
        self.aids_row.set_states({})

    def _save_ghost(self):
        points = self.track_map.get_trail_points("atual")
        if len(points) > 10:
            self._ghost_points = points

    def _draw_ghost(self):
        for x, z in self._ghost_points:
            self.track_map.append_point("ghost", GHOST_COLOR, x, z)
