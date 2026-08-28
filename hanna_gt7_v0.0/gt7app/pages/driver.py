"""
Página de perfil do piloto — o que se repete, volta após volta.

As outras páginas olham uma volta. Esta olha o piloto: sobre a janela de voltas
gravadas de uma pista, o que é hábito e não acaso.

A honestidade estatística do módulo aparece na tela. Com poucas voltas o perfil
vem marcado como preliminar e o cabeçalho diz isso — em vez de exibir um desvio
padrão de três amostras com cara de veredito. É a mesma decisão de
`DriverProfile.is_reliable`, só que visível.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from gt7core.analytics.driver import DriverProfile, build_profile
from gt7core.analytics.tyres import stint_degradation
from gt7core.domain.models import Lap, TelemetryPoint

from ..application import CoreApplication
from ..design.theme import OBJ_SECTION_TITLE
from ..design.tokens import Palette, Space, Theme
from ..widgets.advice import AdviceCard
from ..widgets.cards import Badge, Card, MetricCard, MetricGrid, StatRow
from ..widgets.charts import DistanceChart, Series
from ..widgets.selectors import format_lap_time
from .base import Page

# Teto de voltas carregadas para o perfil. Coincide com a janela de retenção
# padrão por pista (Fase 3): é a mesma "janela recente" em outra forma.
PROFILE_LAP_LIMIT = 20


class DriverPage(Page):
    # `page_id` continua "driver": ele é chave de navegação e de comando, e
    # renomeá-lo quebraria atalho salvo sem ganhar nada — o que a pessoa lê é
    # `nav_title`.
    page_id = "driver"
    nav_title = "Análise de stint"
    title = "Análise de stint"
    subtitle = "O que se repete volta após volta — consistência, evolução e desgaste"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        super().__init__(core, theme)

    def build(self) -> None:
        self.header.add_action(self._build_toolbar())

        self._badge = Badge("")
        self._badge.setVisible(False)
        self.content.addWidget(self._badge)

        self._summary = MetricGrid(columns=5)
        for key, label, unit in (
            ("laps", "Voltas", ""),
            ("best", "Melhor", ""),
            ("consistency", "Desvio", "s"),
            ("trend", "Tendência", "s/volta"),
            ("errors", "Erros/volta", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        columns = QHBoxLayout()
        columns.setSpacing(Space.LG.px)

        self._strengths = Card("Pontos fortes")
        self._weaknesses = Card("A trabalhar")
        columns.addWidget(self._strengths, stretch=1)
        columns.addWidget(self._weaknesses, stretch=1)
        self.content.addLayout(columns)

        detail = Card("Medidas")
        self._rows = {
            key: StatRow(label)
            for key, label in (
                ("style", "Estilo de frenagem"),
                ("trail", "Trail braking médio"),
                ("brake_repeat", "Repetibilidade da frenagem"),
                ("throttle", "Retomada média após o ápice"),
                ("lockups", "Travamentos por volta"),
                ("spins", "Patinagens por volta"),
                ("lifts", "Alívios por volta"),
                ("degradation", "Degradação no stint"),
            )
        }
        for row in self._rows.values():
            detail.add(row)
        self.content.addWidget(detail)

        pace_card = Card("Ritmo ao longo da janela")
        self._pace_chart = DistanceChart(
            self.theme, "Tempo de volta", unit="s", height=140, x_unit="ª volta"
        )
        pace_card.add(self._pace_chart)
        self.content.addWidget(pace_card)

        self._advice = AdviceCard(self.theme, "Relatório de sessão")
        self.content.addWidget(self._advice)

        service = self.core.engineer_service
        if service is None or not service.is_available:
            self._advice.show_unavailable()
        else:
            self._advice.show_idle("Selecione uma pista para o relatório.")
            service.started.connect(self._on_engineer_started)
            service.ready.connect(self._on_advice)
            service.failed.connect(self._advice.show_error)
        self.content.addStretch(1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._track_combo = QComboBox()
        self._track_combo.currentIndexChanged.connect(lambda _: self._rebuild())

        self._from_spin = QSpinBox()
        self._from_spin.setMinimum(1)
        self._from_spin.setMaximum(1)
        self._to_spin = QSpinBox()
        self._to_spin.setMinimum(1)
        self._to_spin.setMaximum(1)
        for spin in (self._from_spin, self._to_spin):
            spin.valueChanged.connect(lambda _: self._rebuild())

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_combo)
        layout.addWidget(QLabel("Voltas:"))
        layout.addWidget(self._from_spin)
        layout.addWidget(QLabel("a"))
        layout.addWidget(self._to_spin)
        return bar

    # ---------- dados ----------

    def _sync_range(self, total: int) -> None:
        """Ajusta os limites dos seletores ao que existe na pista.

        Sem isto, trocar de uma pista com 20 voltas para outra com 3 deixaria
        "voltas 5 a 12" selecionado e o perfil sairia vazio, sem explicação.
        """
        for spin in (self._from_spin, self._to_spin):
            spin.blockSignals(True)
            spin.setMaximum(max(1, total))
            spin.setMinimum(1)
        if self._to_spin.value() > total or self._to_spin.value() <= 1:
            self._to_spin.setValue(max(1, total))
        if self._from_spin.value() > total:
            self._from_spin.setValue(1)
        for spin in (self._from_spin, self._to_spin):
            spin.blockSignals(False)

    def refresh(self) -> None:
        previous = self._track_combo.currentData()
        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        for track in self.core.tracks.get_all():
            self._track_combo.addItem(track.name, track.id)
        if previous is not None:
            index = self._track_combo.findData(previous)
            if index >= 0:
                self._track_combo.setCurrentIndex(index)
        self._track_combo.blockSignals(False)
        self._rebuild()

    def _rebuild(self) -> None:
        track_id = self._track_combo.currentData()
        if track_id is None:
            self._clear("nenhuma pista gravada ainda")
            return

        laps = self.core.laps.get_by_track(int(track_id), limit=PROFILE_LAP_LIMIT)
        # Ordem cronológica: a tendência de ritmo depende disso, e o repositório
        # devolve da mais recente para a mais antiga.
        laps = list(reversed(laps))

        # Recorte de voltas. Um perfil sobre a sessão inteira mistura as voltas
        # de reconhecimento com as de ritmo, e a "consistência" resultante
        # descreve um piloto que não existe. Escolher a faixa é o que separa
        # "como eu piloto" de "como eu piloto quando estou tentando".
        self._sync_range(len(laps))
        inicio = max(1, self._from_spin.value())
        fim = min(len(laps), self._to_spin.value())
        if inicio > fim:
            inicio, fim = fim, inicio
        laps = laps[inicio - 1 : fim]

        point_lists: list[list[TelemetryPoint]] = []
        for lap in laps:
            if lap.id is None:
                continue
            points = self.core.laps.load_points(lap.id)
            if len(points) >= 2:
                point_lists.append(points)

        profile = build_profile(point_lists)
        if profile is None:
            self._clear("nenhuma volta com amostras nesta pista")
            return

        self._populate(profile, point_lists)
        self._request_report(profile, laps)

    def _clear(self, message: str) -> None:
        self._summary.clear_values(self.theme)
        self._badge.setText(message)
        self._badge.setVisible(True)
        self._strengths.clear_content()
        self._weaknesses.clear_content()
        for row in self._rows.values():
            row.set_value("—")
        self._pace_chart.clear()

    # ---------- engenheiro ----------

    def _request_report(self, profile: DriverProfile, laps: list[Lap]) -> None:
        """Pede o relatório da janela de voltas exibida.

        Nível 3 é a única chamada que olha o conjunto, e por isso a única que
        pode falar de tendência — os tempos volta a volta vão junto porque
        média e desvio não mostram **forma**: três voltas boas seguidas de queda
        conta uma história diferente de oscilação constante com a mesma média.
        """
        service = self.core.engineer_service
        if service is None or not service.is_available:
            return

        service.request_session_report(
            profile,
            track=self._track_combo.currentText(),
            lap_times_ms=[lap.lap_time_ms for lap in laps if lap.lap_time_ms > 0],
        )

    def _on_engineer_started(self, level: str) -> None:
        if level == "session":
            self._advice.show_thinking()

    def _on_advice(self, advice: object) -> None:
        if str(getattr(getattr(advice, "level", ""), "value", "")) == "session":
            self._advice.show_advice(advice)

    def _populate(
        self, profile: DriverProfile, point_lists: list[list[TelemetryPoint]]
    ) -> None:
        palette = self.theme.palette

        if profile.is_reliable:
            self._badge.setVisible(False)
        else:
            self._badge.setText(
                f"Perfil preliminar — {profile.lap_count} volta(s). "
                "As médias já valem; o desvio ainda não descreve o piloto."
            )
            self._badge.set_color(palette.yellow)
            self._badge.setVisible(True)

        cards = self._summary.cards
        cards["laps"].set_value(str(profile.lap_count))
        cards["best"].set_value(format_lap_time(profile.best_lap_ms))
        cards["consistency"].set_value(
            f"{profile.lap_time_stddev_ms / 1000:.3f}",
            _consistency_colour(profile, palette),
        )
        trend = profile.pace_trend_ms_per_lap / 1000.0
        cards["trend"].set_value(f"{trend:+.3f}", palette.delta(trend))
        cards["errors"].set_value(
            f"{profile.error_rate_per_lap:.1f}",
            palette.green if profile.error_rate_per_lap < 0.5 else palette.yellow,
        )

        _fill_card(self._strengths, profile.strengths(), palette.green, "—")
        _fill_card(self._weaknesses, profile.weaknesses(), palette.yellow, "nada a apontar")

        rows = self._rows
        rows["style"].set_value(profile.braking_style)
        rows["trail"].set_value(f"{profile.average_trail_braking:.2f}")
        rows["brake_repeat"].set_value(
            f"±{profile.braking_point_stddev_m:.1f} m"
            if profile.braking_point_stddev_m is not None
            else "—"
        )
        rows["throttle"].set_value(
            f"{profile.average_throttle_delay_m:.0f} m"
            if profile.average_throttle_delay_m is not None
            else "—"
        )
        rows["lockups"].set_value(f"{profile.lockups_per_lap:.1f}")
        rows["spins"].set_value(f"{profile.wheelspins_per_lap:.1f}")
        rows["lifts"].set_value(f"{profile.lifts_per_lap:.1f}")

        degradation = stint_degradation(point_lists)
        rows["degradation"].set_value(
            degradation.describe() if degradation else "—"
        )

        # O "eixo de distância" aqui é o número da volta — o widget é o mesmo,
        # e reusá-lo evita um segundo gráfico quase idêntico.
        self._pace_chart.set_series(
            [
                Series(
                    "volta",
                    palette.accent,
                    [
                        (float(index + 1), points[-1].elapsed_ms / 1000.0)
                        for index, points in enumerate(point_lists)
                    ],
                )
            ]
        )


def _consistency_colour(profile: DriverProfile, palette: Palette) -> str:
    label = profile.consistency_label
    if label == "consistente":
        return palette.green
    return palette.yellow if label == "regular" else palette.red


def _fill_card(card: Card, notes: list[str], colour: str, empty: str) -> None:
    card.clear_content()
    if not notes:
        label = QLabel(empty)
        label.setObjectName(OBJ_SECTION_TITLE)
        card.add(label)
        return
    for note in notes:
        label = QLabel(f"•  {note}")
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {colour}; background: transparent;")
        card.add(label)
