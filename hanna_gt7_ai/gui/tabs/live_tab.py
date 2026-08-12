"""
Aba "Ao Vivo" — o dashboard de telemetria em tempo real.
"""

from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QLabel, QHBoxLayout

from gui.widgets import MetricCard, BarCard, DeltaCard, format_ms
from gui.widgets_chart import LiveStripChart, TrackMapWidget
from gui.widgets_tire import TireTempPanel

TRAIL_COLOR = "#4f7cff"
MARKER_COLOR = "#3ddc84"


class LiveDashboardTab(QWidget):
    def __init__(self):
        super().__init__()
        self._last_lap_count = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 16, 4, 4)
        root.setSpacing(14)

        # ---------- linha 1: delta em destaque ----------
        self.card_delta = DeltaCard()
        root.addWidget(self.card_delta)

        # ---------- linha 2: métricas principais ----------
        metrics_grid = QGridLayout()
        metrics_grid.setSpacing(14)

        self.card_speed = MetricCard("Velocidade", " km/h")
        self.card_rpm = MetricCard("RPM")
        self.card_gear = MetricCard("Marcha")
        self.card_lap = MetricCard("Volta")
        self.card_laptime = MetricCard("Tempo de volta")
        self.card_fuel = MetricCard("Combustível", "%")

        metrics_grid.addWidget(self.card_speed, 0, 0)
        metrics_grid.addWidget(self.card_rpm, 0, 1)
        metrics_grid.addWidget(self.card_gear, 0, 2)
        metrics_grid.addWidget(self.card_lap, 0, 3)
        metrics_grid.addWidget(self.card_laptime, 1, 0)
        metrics_grid.addWidget(self.card_fuel, 1, 1)
        root.addLayout(metrics_grid)

        # ---------- linha 3: acelerador/freio, barra + gráfico ao vivo ----------
        pedals_row = QHBoxLayout()
        pedals_row.setSpacing(14)

        throttle_col = QVBoxLayout()
        throttle_col.setSpacing(6)
        self.card_throttle = BarCard("Acelerador", "#3ddc84")
        self.chart_throttle = LiveStripChart("Acelerador (%) — últimos segundos", "#3ddc84")
        throttle_col.addWidget(self.card_throttle)
        throttle_col.addWidget(self.chart_throttle)

        brake_col = QVBoxLayout()
        brake_col.setSpacing(6)
        self.card_brake = BarCard("Freio", "#ff5c5c")
        self.chart_brake = LiveStripChart("Freio (%) — últimos segundos", "#ff5c5c")
        brake_col.addWidget(self.card_brake)
        brake_col.addWidget(self.chart_brake)

        pedals_row.addLayout(throttle_col)
        pedals_row.addLayout(brake_col)
        root.addLayout(pedals_row)

        # ---------- linha 4: pneus, layout de carro ----------
        tire_label = QLabel("TEMPERATURA DOS PNEUS")
        tire_label.setStyleSheet("color: #8a8e99; font-size: 12px; font-weight: 600; letter-spacing: 1px;")
        root.addWidget(tire_label)

        self.tire_panel = TireTempPanel()
        root.addWidget(self.tire_panel)

        # ---------- linha 5: traçado da volta (posição real X-Z) ----------
        # Usa position_x/position_z, já decodificados de todo pacote do
        # GT7 (telemetry/gt7_protocol.py) mas até então descartados — não é
        # um mapa oficial da pista (o GT7 não fornece um), é a trajetória
        # real percorrida nesta volta.
        self.track_map = TrackMapWidget("Traçado da volta atual")
        root.addWidget(self.track_map)

    def render_frame(self, frame):
        self.card_speed.set_value(f"{frame.speed_kmh:.0f}")
        self.card_rpm.set_value(f"{frame.rpm:.0f}")
        # gear vem de um nibble sem sinal (0-15): 0 é Neutro na convenção
        # universal dos decodificadores públicos do GT7. O protocolo aqui
        # não tem um offset de "marcha à ré" validado com dados reais, então
        # não inventamos um valor "R" — mostramos o número bruto se algum
        # dia vier fora da faixa 0-8 esperada, em vez de rotular errado.
        self.card_gear.set_value("N" if frame.gear == 0 else frame.gear)
        self.card_lap.set_value(f"{frame.lap_count}/{frame.total_laps}")
        self.card_laptime.set_value(format_ms(frame.current_lap_ms))
        self.card_fuel.set_value(f"{frame.fuel:.0f}")

        self.card_throttle.set_value(frame.throttle)
        self.card_brake.set_value(frame.brake)
        self.chart_throttle.push(frame.throttle)
        self.chart_brake.push(frame.brake)

        self.tire_panel.set_temps(
            frame.tire_temp_fl, frame.tire_temp_fr,
            frame.tire_temp_rl, frame.tire_temp_rr,
        )

        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self.track_map.clear()
        self._last_lap_count = frame.lap_count
        self.track_map.append_point("atual", TRAIL_COLOR, frame.position_x, frame.position_z)
        self.track_map.set_marker(frame.position_x, frame.position_z, MARKER_COLOR)

    def render_delta(self, delta_seconds):
        self.card_delta.set_delta(delta_seconds)

    def render_stale(self):
        """Estado neutro do item 9: sem telemetria nova, mostra valores
        neutros em vez de congelar o último dado real recebido — evita
        confundir 'carro parado' (valor real 0) com 'sem transmissão'
        (o indicador de conexão no topo da janela já cobre essa distinção)."""
        self.card_speed.set_value("0")
        self.card_rpm.set_value("0")
        self.card_gear.set_value("N")
        self.card_lap.set_value("--/--")
        self.card_laptime.set_value(format_ms(None))
        self.card_fuel.set_value("0")
        self.card_throttle.set_value(0)
        self.card_brake.set_value(0)
