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
    QWidget,
)

from gt7core.analytics.coaching import CornerReport, diagnose_corners
from gt7core.analytics.driver import DriverProfile, build_profile
from gt7core.analytics.tyres import stint_degradation
from gt7core.domain.models import Lap, TelemetryPoint

from ..application import CoreApplication
from ..design.theme import OBJ_SECTION_TITLE
from ..design.tokens import Palette, Space, Theme
from ..widgets.advice import AdviceCard
from ..widgets.cards import Badge, Card, MetricCard, MetricGrid, StatRow
from ..widgets.charts import DistanceChart, Series
from ..widgets.flow import FlowWidget, labelled
from ..widgets.selectors import (
    LAP_COMBO_MIN_W,
    TRACK_COMBO_MIN_W,
    format_lap_time,
)
from .base import Page

# Teto de voltas carregadas para o perfil. Coincide com a janela de retenção
# padrão por pista (Fase 3): é a mesma "janela recente" em outra forma.
PROFILE_LAP_LIMIT = 20

#: Largura mínima do combo de carro. Nomes do GT7 são longos — "Porsche 911
#: GT3 RS (992) '22" —, e cortado no meio um modelo deixa de identificar o
#: carro, que é a única coisa que o filtro precisa fazer.
CAR_COMBO_MIN_W = 200

#: Quantas curvas aparecem em "A trabalhar", e quantos apontamentos por
#: curva. Uma lista longa deixa de ser conselho e vira relatório: quem vai
#: para a pista leva duas ou três correções na cabeça, não quinze.
MAX_CORNERS_SHOWN = 4
MAX_ISSUES_PER_CORNER = 2


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
        # Quatro seletores numa linha rígida pediam 1362 px só de cabeçalho, e
        # a página nascia cortada com o último inalcançável. Aqui eles quebram
        # para a linha de baixo quando a janela aperta.
        bar = FlowWidget(spacing=Space.SM.px, align_right=True)
        layout = bar

        self._track_combo = QComboBox()
        self._track_combo.setMinimumWidth(TRACK_COMBO_MIN_W)
        self._track_combo.currentIndexChanged.connect(lambda _: self._rebuild())

        # Filtro de carro. Um stint é de **um** carro: misturar o GT3 com o
        # carro de rua na mesma janela produz um desvio padrão que descreve a
        # troca de carro, não a consistência de quem pilota.
        self._car_combo = QComboBox()
        self._car_combo.setMinimumWidth(CAR_COMBO_MIN_W)
        self._car_combo.currentIndexChanged.connect(lambda _: self._rebuild())

        # Voltas por dropdown, e não por número.
        #
        # Aqui havia dois QSpinBox contando ordinais — "da 3ª à 12ª". Escolher
        # assim exige ir ao Histórico, contar as linhas e voltar, porque a
        # janela útil de um stint se identifica pelo tempo da volta, não pela
        # posição dela na lista. Os combos mostram tempo e número da volta, que
        # é o que se reconhece.
        self._from_combo = QComboBox()
        self._to_combo = QComboBox()
        for combo in (self._from_combo, self._to_combo):
            combo.setMinimumWidth(LAP_COMBO_MIN_W)
            combo.currentIndexChanged.connect(lambda _: self._rebuild())

        # Cada par rótulo+seletor é um bloco: a barra quebra **entre** eles,
        # nunca no meio de um.
        layout.addWidget(labelled("Pista:", self._track_combo))
        layout.addWidget(labelled("Carro:", self._car_combo))
        layout.addWidget(labelled("De:", self._from_combo))
        layout.addWidget(labelled("a", self._to_combo))
        return bar

    # ---------- dados ----------

    def _reload_cars(self, laps: list[Lap]) -> None:
        """Repõe a lista com os carros que **esta pista** tem gravados.

        Só os desta pista, pelo mesmo motivo do Histórico: o catálogo inteiro
        ofereceria filtros que não filtram nada. A escolha atual sobrevive à
        reposição quando o carro ainda existe.
        """
        anterior = self._car_combo.currentData()
        ids = {x.car_id for x in laps if x.car_id is not None}

        self._car_combo.blockSignals(True)
        self._car_combo.clear()
        self._car_combo.addItem("todos", None)
        for car_id in sorted(ids, key=lambda i: self.car_name(i).lower()):
            self._car_combo.addItem(self.car_name(car_id), car_id)
        indice = self._car_combo.findData(anterior)
        self._car_combo.setCurrentIndex(max(0, indice))
        self._car_combo.blockSignals(False)

    def _sync_range(self, laps: list[Lap]) -> None:
        """Repõe os combos de volta com o que existe na janela atual.

        Guarda a escolha por **id de volta**, e não por posição: trocar o
        filtro de carro reordena a lista, e um índice preservado passaria a
        apontar para outra volta em silêncio. O id ou existe na lista nova, e a
        escolha é a mesma volta, ou não existe, e a queda é para a ponta.

        Sem isto, trocar de uma pista com 20 voltas para outra com 3 deixaria
        "voltas 5 a 12" selecionado e o perfil sairia vazio, sem explicação.
        """
        anterior_de = self._from_combo.currentData()
        anterior_ate = self._to_combo.currentData()

        for combo in (self._from_combo, self._to_combo):
            combo.blockSignals(True)
            combo.clear()

        for ordem, lap in enumerate(laps, start=1):
            rotulo = f"{ordem}ª  {format_lap_time(lap.lap_time_ms)}"
            self._from_combo.addItem(rotulo, lap.id)
            self._to_combo.addItem(rotulo, lap.id)

        # Padrão: a janela inteira. É o que alguém espera ao abrir a página,
        # e recortar é uma decisão que se toma depois de ver o conjunto.
        self._restore_or(self._from_combo, anterior_de, 0)
        self._restore_or(self._to_combo, anterior_ate, self._to_combo.count() - 1)

        for combo in (self._from_combo, self._to_combo):
            combo.blockSignals(False)

    @staticmethod
    def _restore_or(combo: QComboBox, lap_id: object, fallback: int) -> None:
        indice = combo.findData(lap_id) if lap_id is not None else -1
        combo.setCurrentIndex(indice if indice >= 0 else max(0, fallback))

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

        todas = self.core.laps.get_by_track(int(track_id), limit=PROFILE_LAP_LIMIT)
        # Ordem cronológica: a tendência de ritmo depende disso, e o repositório
        # devolve da mais recente para a mais antiga.
        todas = list(reversed(todas))

        self._reload_cars(todas)
        car_id = self._car_combo.currentData()
        laps = todas if car_id is None else [x for x in todas if x.car_id == car_id]

        # Recorte de voltas. Um perfil sobre a sessão inteira mistura as voltas
        # de reconhecimento com as de ritmo, e a "consistência" resultante
        # descreve um piloto que não existe. Escolher a faixa é o que separa
        # "como eu piloto" de "como eu piloto quando estou tentando".
        self._sync_range(laps)
        inicio = self._from_combo.currentIndex()
        fim = self._to_combo.currentIndex()
        if inicio < 0 or fim < 0:
            self._clear("nenhuma volta gravada nesta pista")
            return
        if inicio > fim:
            inicio, fim = fim, inicio
        laps = laps[inicio : fim + 1]

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

        reports = diagnose_corners(point_lists)
        self._populate(profile, point_lists, reports)
        self._request_report(profile, laps, reports)

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

    def _request_report(
        self,
        profile: DriverProfile,
        laps: list[Lap],
        reports: list[CornerReport],
    ) -> None:
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
            recurring=_recurring_block(reports),
        )

    def _on_engineer_started(self, level: str) -> None:
        if level == "session":
            self._advice.show_thinking()

    def _on_advice(self, advice: object) -> None:
        if str(getattr(getattr(advice, "level", ""), "value", "")) == "session":
            self._advice.show_advice(advice)

    def _populate(
        self,
        profile: DriverProfile,
        point_lists: list[list[TelemetryPoint]],
        reports: list[CornerReport],
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
        # "A trabalhar" passa a ser por curva. As contagens da volta inteira
        # — 7 travamentos, ±25 m de dispersão — continuam existindo, e ficam
        # no cartão "Medidas" logo abaixo: lá elas são o que são, um resumo.
        # Aqui o que se quer é o que fazer na próxima volta, e isso é sempre
        # sobre uma curva.
        self._fill_weaknesses(reports, profile)

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


    def _fill_weaknesses(
        self, reports: list[CornerReport], profile: DriverProfile
    ) -> None:
        """Apontamentos por curva, da mais grave para a menos.

        Teto de `MAX_CORNERS_SHOWN` curvas: uma lista com quinze itens não é
        lida, é rolada, e quem sai para a pista leva duas ou três coisas na
        cabeça. As piores primeiro é o que faz o corte valer.

        Sem apontamento nenhum, cai nas observações gerais do perfil — que
        falam de consistência e ritmo, coisas que não moram numa curva. Só
        quando também não há nenhuma delas é que a tela diz que está tudo bem.
        """
        linhas: list[str] = []
        for relatorio in reports[:MAX_CORNERS_SHOWN]:
            linhas.extend(relatorio.as_lines()[:MAX_ISSUES_PER_CORNER])

        if not linhas:
            linhas = profile.weaknesses()

        _fill_card(
            self._weaknesses,
            linhas,
            self.theme.palette.yellow,
            "nada a apontar",
        )


def _recurring_block(reports: list[CornerReport]) -> str:
    """Os apontamentos por curva, no formato que o prompt do engenheiro espera.

    O parâmetro `recurring` do `session_report` já existia e ninguém preenchia;
    sem ele o modelo recebia médias da volta inteira e, para falar de curva —
    que é como um engenheiro fala —, teria que **adivinhar** qual. Adivinhar
    número de curva é exatamente o tipo de grandeza que o prompt proíbe
    inventar, então ele calava ou generalizava.

    Agora recebe a medição, com curva e frequência. O modelo continua sem poder
    inventar; a diferença é que não precisa mais.
    """
    if not reports:
        return ""
    linhas = [
        linha
        for relatorio in reports[:MAX_CORNERS_SHOWN]
        for linha in relatorio.as_lines()[:MAX_ISSUES_PER_CORNER]
    ]
    if not linhas:
        return ""
    return "Recorrente por curva (medido):\n" + "\n".join(f"- {x}" for x in linhas)


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
