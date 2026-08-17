"""
Demonstração do núcleo rodando — sem PS5, sem Qt, sem banco.

    python3 -m gt7core.demo

Existe por dois motivos. O primeiro é prático: dá para ver a plataforma
funcionando sem instalar interface gráfica nem ter um console na rede. O
segundo é que ela é a prova executável do que a Fase 1 entregou — todo o
pipeline (fonte → motor → eventos → analytics) roda aqui em Python puro.

O fluxo é exatamente o mesmo do ao vivo. Trocar `MockTelemetrySource` por
`Gt7UdpTelemetrySource` não muda mais nenhuma linha deste arquivo — é o que o
contrato de fonte compra.
"""

from __future__ import annotations

import argparse
import sys
import time

from .analytics.braking import detect_braking_zones
from .analytics.corners import detect_corners
from .analytics.delta import LapComparator
from .analytics.driver import build_profile
from .analytics.series import LapSeries
from .analytics.throttle import analyse_throttle
from .analytics.timeloss import analyse_time_loss
from .analytics.tyres import detect_tyre_events, temperature_balance
from .config.settings import Settings
from .events.bus import EventBus
from .observability.logging import configure_logging
from .telemetry.engine import LapBoundaryDetected, TelemetryEngine, TelemetryReceived
from .telemetry.sources.mock import synthetic_session


def format_lap_time(total_ms: int) -> str:
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


class DemoReport:
    """Assina o barramento e narra o que acontece — o papel que a UI terá."""

    def __init__(self, bus: EventBus) -> None:
        self.laps: list[LapBoundaryDetected] = []
        self.best: LapBoundaryDetected | None = None
        self.frames = 0
        self.top_speed = 0.0
        self.max_braking_g = 0.0

        bus.subscribe(TelemetryReceived, self._on_telemetry)
        bus.subscribe(LapBoundaryDetected, self._on_lap)

    def _on_telemetry(self, event: TelemetryReceived) -> None:
        self.frames += 1
        point = event.point
        self.top_speed = max(self.top_speed, point.speed_kmh)
        self.max_braking_g = min(self.max_braking_g, point.g_longitudinal)

    def _on_lap(self, event: LapBoundaryDetected) -> None:
        self.laps.append(event)

        marker = "     "
        if self.best is None or event.lap_time_ms < self.best.lap_time_ms:
            self.best = event
            marker = "  ★  "  # melhor da sessão

        delta_text = ""
        if self.best is not event and self.best is not None:
            delta_s = (event.lap_time_ms - self.best.lap_time_ms) / 1000
            delta_text = f"   {delta_s:+.3f}s vs melhor"

        print(
            f"{marker}Volta {event.lap_number:>2}   "
            f"{format_lap_time(event.lap_time_ms)}   "
            f"{event.distance_m:>7.1f} m   "
            f"{len(event.points):>5} amostras{delta_text}"
        )


def analyse_best_lap(report: DemoReport) -> None:
    """Analytics sobre a melhor volta — canais, setores e delta."""
    if report.best is None:
        return

    series = LapSeries(report.best.points)
    print()
    print("─" * 74)
    print(f"ANÁLISE DA MELHOR VOLTA  (volta {report.best.lap_number})")
    print("─" * 74)

    print(f"  Distância            {series.max_distance:.1f} m")
    print(f"  Duração              {series.max_time:.2f} s")
    print(f"  Velocidade máxima    {report.top_speed:.1f} km/h")
    print(f"  Frenagem máxima      {report.max_braking_g:.2f} g")

    print()
    print("  Velocidade ao longo da volta:")
    for fraction in (0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
        distance = series.max_distance * fraction
        speed = series.value_at(distance, "speed_kmh")
        brake = series.value_at(distance, "brake")
        if speed is None:
            continue
        bar = "█" * int(speed / 8)
        flag = " ← freando" if brake and brake > 20 else ""
        print(f"    {distance:>7.0f} m  {speed:>6.1f} km/h  {bar}{flag}")

    # Delta da última volta contra a melhor — o motor que alimenta o ao vivo.
    if len(report.laps) >= 2:
        last = report.laps[-1]
        if last is not report.best:
            comparator = LapComparator(report.best.points)
            print()
            print(f"  Delta da volta {last.lap_number} contra a melhor:")
            for fraction in (0.25, 0.5, 0.75, 0.95):
                distance = last.distance_m * fraction
                elapsed = LapSeries(last.points).elapsed_ms_at(distance)
                if elapsed is None:
                    continue
                delta_ms = comparator.delta_ms_at(distance, int(elapsed))
                if delta_ms is None:
                    continue
                sign = "+" if delta_ms >= 0 else ""
                print(f"    {distance:>7.0f} m   {sign}{delta_ms / 1000:.3f} s")


def analyse_driving(report: DemoReport) -> None:
    """Engenharia de pista sobre as voltas capturadas — o que a Fase 4 entrega.

    A diferença entre esta seção e a anterior é a pergunta que responde. A de
    cima descreve a volta ("foi tão rápida, tão longa"); esta diz o que fazer
    diferente na próxima.
    """
    if report.best is None:
        return

    best = report.best.points
    corners = detect_corners(best)

    print()
    print("─" * 74)
    print("ENGENHARIA DE PISTA")
    print("─" * 74)

    print(f"  {len(corners)} curvas detectadas na melhor volta:")
    zones = detect_braking_zones(best)
    applications = analyse_throttle(best, corners)
    by_corner = {a.corner_index: a for a in applications}

    for corner in corners:
        radius = f"{corner.radius_m:>5.0f} m" if corner.radius_m else "    —"
        print(
            f"    Curva {corner.index}  ápice {corner.apex_distance_m:>6.0f} m  "
            f"{corner.minimum_speed_kmh:>5.1f} km/h  raio {radius}  {corner.severity}"
        )

    if zones:
        print()
        print("  Frenagens:")
        for number, zone in enumerate(zones, start=1):
            print(
                f"    Zona {number}  início {zone.start_distance_m:>6.0f} m  "
                f"{zone.average_deceleration_g:>4.2f} g  "
                f"pico {zone.max_pressure_pct:>3.0f}%  "
                f"trail {zone.trail_braking_ratio:.2f}"
            )

    if applications:
        print()
        print("  Saídas de curva:")
        for corner in corners:
            application = by_corner.get(corner.index)
            if application is not None:
                print(f"    Curva {corner.index}  {application.describe()}")

    events = detect_tyre_events(best)
    if events:
        print()
        print(f"  Perdas de aderência: {len(events)}")
        for event in events[:4]:
            print(f"    {event.describe()}")

    balance = temperature_balance(best)
    if balance is not None:
        print()
        print(f"  Pneus: {balance.describe()}")

    # Onde a última volta se perdeu contra a melhor — a pergunta do §20.
    last = report.laps[-1] if report.laps else None
    if last is not None and last is not report.best:
        print()
        print("─" * 74)
        print(f"ONDE A VOLTA {last.lap_number} FOI PERDIDA (contra a melhor)")
        print("─" * 74)
        for line in analyse_time_loss(best, last.points).summary().splitlines():
            print(f"  {line}")

    profile = build_profile([lap.points for lap in report.laps])
    if profile is not None:
        print()
        print("─" * 74)
        print("PERFIL DO PILOTO")
        print("─" * 74)
        for line in profile.summary().splitlines():
            print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gt7core.demo",
        description="Roda o núcleo da plataforma GT7 com telemetria sintética.",
    )
    parser.add_argument("--laps", type=int, default=5, help="voltas a simular")
    parser.add_argument("--verbose", action="store_true", help="log detalhado")
    args = parser.parse_args(argv)

    settings = Settings.load()
    configure_logging("DEBUG" if args.verbose else "WARNING")

    print()
    print("=" * 74)
    print("  HANNA GT7 — núcleo de telemetria")
    print("=" * 74)
    print(f"  Fonte          {settings.telemetry.source} (sintética, sem PS5)")
    print(f"  Taxa           {settings.telemetry.sample_rate_hz} Hz")
    print(f"  IA             {'ligada' if settings.ai.enabled else 'desligada'}")
    print(f"  Qt carregado   {'sim' if 'PySide6' in sys.modules else 'não'}")
    print("=" * 74)
    print()

    bus = EventBus()
    engine = TelemetryEngine(bus, sample_rate_hz=settings.telemetry.sample_rate_hz)
    report = DemoReport(bus)

    started = time.monotonic()
    for frame in synthetic_session(lap_count=args.laps):
        engine.on_frame(frame)
    duration = time.monotonic() - started

    analyse_best_lap(report)
    analyse_driving(report)

    print()
    print("─" * 74)
    rate = report.frames / duration if duration > 0 else 0
    print(
        f"  {report.frames} amostras processadas em {duration:.2f} s "
        f"({rate:,.0f} amostras/s — {rate / 60:.0f}× tempo real)"
    )
    print(f"  {len(report.laps)} voltas fechadas")
    print("─" * 74)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
