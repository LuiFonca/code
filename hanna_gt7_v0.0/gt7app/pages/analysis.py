"""
Página de análise — uma volta, dissecada.

É aqui que a Fase 4 fica visível. Até agora curvas, frenagem, acelerador e
pneus só existiam no terminal, via `python3 -m gt7core.demo`; esta página é a
interface deles.

Organização: em cima, uma faixa com as quatro leituras que são **formas** —
traçado, força G, temperatura dos pneus e o cursor —, lado a lado; embaixo dela,
os canais por distância na largura inteira da página; e no rodapé a tabela de
curvas com o que foi medido em cada uma. Passar o mouse por qualquer gráfico
move o cursor de todos e destaca a curva correspondente — é a leitura que um
engenheiro faz, relacionando o que aconteceu no pedal com onde aconteceu na
pista.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from gt7core.analytics.aids import AIDS, aid_spans, unknown_bits, was_recorded
from gt7core.analytics.braking import BrakingZone, detect_braking_zones
from gt7core.analytics.corners import Corner, corner_at, detect_corners
from gt7core.analytics.elevation import elevation_range_m, slope_series
from gt7core.analytics.series import sector_boundaries_m
from gt7core.analytics.steering import yaw_rate_series
from gt7core.analytics.tyres import (
    MIN_SPEED_FOR_SLIP_KMH,
    WHEELS,
    detect_tyre_events,
    infer_slip_convention,
    slip_ratio,
    temperature_balance,
)
from gt7core.domain.models import TelemetryPoint

from ..application import CoreApplication
from ..design.tokens import Space, Theme
from ..widgets.aidband import AidBand
from ..widgets.cards import Card, MetricCard, MetricGrid, StatRow
from ..widgets.charts import (
    SPEED_STEP_KMH,
    SPEED_TOP_MIN_KMH,
    DistanceChart,
    Series,
)
from ..widgets.gforce import SCALE_STEPS_G, GForceDiagram
from ..widgets.selectors import TrackLapSelector, format_lap_time
from ..widgets.trackmap import TrackMap, TrackMarker, TrackPath
from ..widgets.tyres import TyreTemperatures
from .base import Page

CORNER_COLUMNS = ("Curva", "Ápice", "Vel. mín.", "Freada")

# Os mesmos setores do histórico — o corte precisa ser o mesmo entre telas.
NUM_SECTORS = 3

#: Índices em `self._charts`. Nomeados porque `self._charts[3]` num arquivo de
#: 500 linhas não diz qual gráfico é, e trocar dois por engano é um defeito que
#: só aparece olhando a tela.
CHART_SPEED, CHART_PEDALS, CHART_BOOST = 0, 1, 2
CHART_YAW, CHART_GRIP, CHART_HEIGHT, CHART_SLOPE = 3, 4, 5, 6

#: Rótulo de cada roda no gráfico de aderência, na ordem de `WHEELS`.
WHEEL_LABELS = {"fl": "DE", "fr": "DD", "rl": "TE", "rr": "TD"}

#: Degraus do eixo de guinada, em °/s. Fixo pelo mesmo motivo da velocidade:
#: escala colada no pico faz duas voltas parecidas parecerem diferentes.
YAW_STEP_DEG = 30.0
YAW_TOP_MIN_DEG = 60.0

#: Amplitude mínima do quadro de aderência, em pontos percentuais. Existe
#: para o outro extremo do problema: sem piso, uma volta limpa — em que as
#: quatro rodas ficam entre 99% e 101% — teria essa oscilação de 2 pontos
#: esticada até a altura toda e leria como um problema grave.
GRIP_MIN_SPAN_PCT = 20.0

#: Amplitude mínima do quadro de altura, em milímetros. Um carro de rua
#: mexe uns 30 mm entre freada e aceleração; um GT3 bem menos. O piso
#: impede que a diferença de um milímetro num carro rígido desenhe do
#: mesmo tamanho que trinta num carro mole.
HEIGHT_MIN_SPAN_MM = 25.0

#: Curso mínimo, em milímetros, para o canal de altura valer um quadro.
#: Abaixo de um milímetro em toda a volta a suspensão não se mexeu, e o que
#: se desenharia é uma reta — ou o campo não foi gravado, ou o carro está
#: parado. Nos dois casos o quadro só ocuparia espaço.
MIN_HEIGHT_TRAVEL_MM = 1.0

#: Pressão de turbo: degraus de 1 bar, teto mínimo de 2. Um carro que faz 1,4
#: bar desenha num quadro de 2; um de 2,4 bar sobe para 3, e assim por diante.
#: Mesmo motivo do diagrama G-G: com degrau fixo, um turbo maior *parece* maior
#: em vez de ser reescalado para preencher o mesmo quadro.
BOOST_STEP_BAR = 1.0
BOOST_TOP_MIN_BAR = 2.0

#: Inclinação em %, com degrau de 5 e teto mínimo de 10. Bathurst tem trechos
#: de 16%; Suzuka fica quase toda abaixo de 5. Com degrau fixo, a mesma rampa
#: desenha do mesmo tamanho em qualquer pista — que é o que permite comparar.
SLOPE_STEP_PCT = 5.0
SLOPE_TOP_MIN_PCT = 10.0

#: Fração da volta que precisa ter rampa medida para o gráfico aparecer. Abaixo
#: disso a linha sairia picotada, e uma linha com buracos num canal de análise
#: é pior que canal nenhum: parece medição e é lacuna.
MIN_SLOPE_COVERAGE = 0.5

#: Desnível a partir do qual vale anunciá-lo no título, em metros. Abaixo disso
#: é ondulação de asfalto, não relevo de circuito.
MIN_INTERESTING_ELEVATION_M = 5.0

#: Canais que o mapa sabe colorir: (rótulo, chave).
MAP_CHANNELS = (
    ("Cor: velocidade", "speed"),
    ("Cor: pedais", "pedals"),
)

#: Pedal considerado acionado, em %. Não é zero: o gatilho analógico do controle
#: repousa em 1–2% e, exigindo zero exato, o mapa inteiro sairia verde.
PEDAL_ON_PCT = 5.0

#: Abaixo deste pico, em bar, o carro é tratado como aspirado.
#:
#: Não é zero: o `turbo_boost` do pacote oscila alguns centésimos em torno de
#: 1,0 mesmo sem turbo, e exigir zero exato marcaria todo carro aspirado como
#: turbinado por causa de ruído.
BOOST_PRESENT_BAR = 0.05


class AnalysisPage(Page):
    page_id = "analysis"
    nav_title = "Análise"
    title = "Análise de volta"
    subtitle = "Curvas, frenagem, acelerador e pneus"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        self._points: list[TelemetryPoint] = []
        self._corners: list[Corner] = []
        self._x_mode = "distance"
        self._lap_time_ms = 0
        #: Cursor travado por clique. Mora na página, e não em cada gráfico,
        #: porque o cursor é um só, compartilhado por cinco gráficos, o mapa e a
        #: faixa de auxílios — com cada widget decidindo por si, metade ficaria
        #: travada e metade seguindo o ponteiro.
        self._frozen = False
        super().__init__(core, theme)

    # ---------- construção ----------

    def build(self) -> None:
        self._selector = TrackLapSelector(self.core.tracks, self.core.laps)
        self._selector.lap_changed.connect(self._on_lap_selected)
        self.header.add_action(self._selector)

        self._summary = MetricGrid(columns=5)
        for key, label, unit in (
            ("time", "Tempo", ""),
            ("corners", "Curvas", ""),
            ("top", "Vel. máxima", "km/h"),
            ("braking", "Freada máx.", "g"),
            ("events", "Perdas aderência", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        channels = Card("Canais por distância")
        self._charts = [
            DistanceChart(
                self.theme,
                "Velocidade",
                unit="km/h",
                height=130,
                y_step=SPEED_STEP_KMH,
                y_top_min=SPEED_TOP_MIN_KMH,
            ),
            DistanceChart(
                self.theme, "Pedais", unit="%", height=110, y_range=(0.0, 100.0)
            ),
            # Turbo logo abaixo dos pedais: é onde ele se lê. Sozinho o canal
            # não diz nada; ao lado do acelerador ele mostra o **atraso** —
            # a distância entre o pé ir ao fundo e a pressão chegar —, e com
            # um quadro de gráfico entre os dois essa comparação exige o olho
            # ir e voltar.
            DistanceChart(
                self.theme,
                "Pressão de turbo",
                unit="bar",
                height=110,
                y_step=BOOST_STEP_BAR,
                y_top_min=BOOST_TOP_MIN_BAR,
            ),
            # Guinada, e não esterço. O GT7 **não transmite volante**, e a
            # leitura dos 296 bytes inteiros confirmou: não há campo de
            # entrada de direção em lugar nenhum do pacote. Houve aqui um
            # gráfico de volante estimado por geometria; ele saiu a pedido,
            # porque estimativa apresentada como canal acaba lida como
            # medição. O que se mede é quanto o carro girou — que é a
            # pergunta por trás de olhar um canal de volante.
            DistanceChart(
                self.theme,
                "Guinada — giro do carro  (+ direita)",
                unit="°/s",
                height=110,
                y_step=YAW_STEP_DEG,
                y_top_min=YAW_TOP_MIN_DEG,
                y_symmetric=True,
            ),
            # Aderência com escala **flutuante**. Com o eixo ancorado em zero
            # e teto de 125%, os dados — que vivem entre 90% e 105% — desenhavam
            # numa faixa de poucos pixels: um travamento de 8 pontos e um de 2
            # eram o mesmo risco. Zero não é referência aqui (roda com 0% de
            # aderência é roda que não gira), então a âncora não estava
            # protegendo nada e custava o canal inteiro.
            DistanceChart(
                self.theme,
                "Aderência por roda  (100% = rodando limpo)",
                unit="%",
                height=120,
                y_anchor_zero=False,
                y_min_span=GRIP_MIN_SPAN_PCT,
            ),
            # Altura de suspensão por roda, em milímetros. Lida junto com a
            # freada e a força G, mostra a transferência de carga: a dianteira
            # afunda ao frear, a traseira ao acelerar, e o lado de fora numa
            # curva. É também onde se vê o carro raspando em zebra ou quebra-
            # -molas — e, num acerto, se a mola está mole demais para o traçado.
            DistanceChart(
                self.theme,
                "Altura por roda",
                unit="mm",
                height=120,
                y_anchor_zero=False,
                y_min_span=HEIGHT_MIN_SPAN_MM,
            ),
            # Inclinação da pista, e não altitude: o que muda a frenagem é a
            # rampa, e o perfil de elevação de uma volta é quase sempre uma
            # curva suave em que não se lê onde a descida começou. A rampa é
            # medida pelo jogo, não derivada da altitude — derivar amplificaria
            # ruído até a linha virar cabeleira.
            DistanceChart(
                self.theme,
                "Inclinação da pista  (+ subida)",
                unit="%",
                height=110,
                y_step=SLOPE_STEP_PCT,
                y_top_min=SLOPE_TOP_MIN_PCT,
                y_symmetric=True,
            ),
        ]
        for chart in self._charts:
            channels.add(chart)
            chart.hovered.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_left)
            chart.clicked.connect(self._on_click)

        # Aviso do canal de escorregamento, logo abaixo do gráfico que ele
        # qualifica. Ver `_fill_grip_chart`.
        self._grip_hint = QLabel("")
        self._grip_hint.setWordWrap(True)
        channels.add(self._grip_hint)

        self._boost_hint = QLabel("")
        self._boost_hint.setWordWrap(True)
        channels.add(self._boost_hint)

        self._slope_hint = QLabel("")
        self._slope_hint.setWordWrap(True)
        channels.add(self._slope_hint)

        # A faixa dos auxílios fecha a coluna de canais, alinhada ao mesmo eixo
        # X: é embaixo de "aderência" que ela se lê, porque TCS atuando e roda
        # patinando são o mesmo evento visto de dois lados.
        self._aid_band = AidBand(self.theme, aids=tuple(AIDS))
        channels.add(self._aid_band)

        self._x_selector = QComboBox()
        self._x_selector.addItems(["Eixo: distância", "Eixo: tempo"])
        self._x_selector.currentIndexChanged.connect(self._on_x_mode)
        channels.add(self._x_selector)

        # ---------- faixa de cima: os quatro quadros quadrados ----------
        #
        # Mapa, força G, pneus e cursor lado a lado. Os três primeiros são
        # **quadrados por natureza** — o conteúdo de cada um é uma forma, não
        # uma série —, e empilhados numa coluna estreita eles disputavam altura
        # com o cursor enquanto os gráficos de linha, que são os que precisam de
        # largura, ficavam com dois terços da tela.
        #
        # Invertido: os quadrados dividem uma faixa horizontal, cada um com o
        # tamanho que a forma pede, e os canais por distância passam a ocupar a
        # **largura inteira** da página. Numa volta de 4 km isso é o que separa
        # ver a freada da curva 7 de ver um risco.
        map_card = Card("Traçado")
        self._map_channel = QComboBox()
        for rotulo, chave in MAP_CHANNELS:
            self._map_channel.addItem(rotulo, chave)
        self._map_channel.currentIndexChanged.connect(self._on_map_channel)
        map_card.add(self._map_channel)

        self._map = TrackMap(self.theme, height=300, heatmap_label="km/h")
        # A ligação nos dois sentidos é o que faz o mapa e os gráficos serem uma
        # leitura só: os gráficos dizem *o que* aconteceu, o mapa diz *onde*.
        self._map.hovered.connect(self._on_hover)
        self._map.hover_left.connect(self._on_hover_left)
        self._map.clicked.connect(self._on_click)
        map_card.add(self._map)

        grip_card = Card("Força G")
        self._gforce = GForceDiagram(self.theme, height=300)
        self._g_scale = QComboBox()
        self._g_scale.addItem("Limite: automático", None)
        for degrau in SCALE_STEPS_G:
            self._g_scale.addItem(f"Limite: {degrau:.0f} g", degrau)
        self._g_scale.currentIndexChanged.connect(self._on_g_scale)
        grip_card.add(self._g_scale)
        grip_card.add(self._gforce)

        tyre_card = Card("Temperatura dos pneus")
        self._tyre_temps = TyreTemperatures(self.theme, height=290)
        self._tyre_caption = QLabel("")
        self._tyre_caption.setWordWrap(True)
        tyre_card.add(self._tyre_temps)
        tyre_card.add(self._tyre_caption)

        self._detail = Card("No cursor")
        self._detail_rows = {
            key: StatRow(label)
            for key, label in (
                ("distance", "Distância"),
                ("speed", "Velocidade"),
                ("throttle", "Acelerador"),
                ("brake", "Freio"),
                ("gear", "Marcha"),
                ("corner", "Curva"),
            )
        }
        for row in self._detail_rows.values():
            self._detail.add(row)

        topo = QHBoxLayout()
        topo.setSpacing(Space.LG.px)
        # O mapa leva mais: o traçado de um circuito é comprido e, apertado, as
        # curvas lentas viram um nó onde nada se distingue.
        topo.addWidget(map_card, stretch=4)
        topo.addWidget(grip_card, stretch=3)
        topo.addWidget(tyre_card, stretch=3)
        topo.addWidget(self._detail, stretch=2)
        self.content.addLayout(topo)

        # ---------- e os canais na largura inteira ----------
        self.content.addWidget(channels)

        # A tabela de curvas embaixo, também na largura inteira. Com quatro
        # colunas ela sobra em espaço, e o que se ganha é a linha inteira
        # legível sem truncar o diagnóstico.
        table_card = Card("Curvas")
        self._table = QTableWidget(0, len(CORNER_COLUMNS))
        self._table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setMinimumHeight(180)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        table_card.add(self._table)
        self.content.addWidget(table_card)

        self._tyres = QLabel("")
        self._tyres.setWordWrap(True)
        self.content.addWidget(self._tyres)

        self._flags_hint = QLabel("")
        self._flags_hint.setWordWrap(True)
        self.content.addWidget(self._flags_hint)

        self._cursor_hint = QLabel("")
        self._cursor_hint.setWordWrap(True)
        self.content.addWidget(self._cursor_hint)

    # ---------- dados ----------

    def show_lap(self, track_id: int, lap_id: int) -> bool:
        """Abre uma volta específica, vinda de outra página.

        A pista primeiro: o combo de voltas só lista as da pista corrente, e
        pedir a volta antes acharia uma lista que ainda é de outro circuito.
        """
        self._selector.reload()
        self._selector.select_track(track_id)
        return self._selector.select_lap(lap_id)

    def refresh(self) -> None:
        self._selector.reload()

    def _on_lap_selected(self, lap_id: object) -> None:
        # O sinal do seletor carrega `object` porque um `Signal(int)` do Qt não
        # transporta None — e "nenhuma volta escolhida" é um estado legítimo.
        if not isinstance(lap_id, int):
            self._clear()
            return

        lap = self.core.laps.get_by_id(lap_id)
        if lap is None:
            self._clear()
            return

        self._points = self.core.laps.load_points(lap_id)
        if len(self._points) < 2:
            self._clear()
            self.header.set_subtitle("volta sem amostras gravadas")
            return

        self._corners = detect_corners(self._points)
        self._selector.set_car(self.car_name(lap.car_id))
        self._populate(lap.lap_time_ms)

    def _clear(self) -> None:
        self._points = []
        self._corners = []
        self._summary.clear_values(self.theme)
        for chart in self._charts:
            chart.clear()
        self._map.clear()
        self._table.setRowCount(0)
        self._tyres.setText("")
        self._flags_hint.setText("")
        self._cursor_hint.setText("")
        self._grip_hint.setText("")
        self._boost_hint.setText("")
        self._slope_hint.setText("")
        self._frozen = False
        for chart in self._charts:
            chart.set_cursor_locked(False)
        self._aid_band.clear()
        self._tyre_temps.clear()
        self._tyre_caption.setText("")
        self._selector.set_car("")

    def _populate(self, lap_time_ms: int) -> None:
        self._lap_time_ms = lap_time_ms
        palette = self.theme.palette
        points = self._points

        zones = detect_braking_zones(points)
        events = detect_tyre_events(points)

        cards = self._summary.cards
        cards["time"].set_value(format_lap_time(lap_time_ms))
        cards["corners"].set_value(str(len(self._corners)))
        cards["top"].set_value(f"{max(p.speed_kmh for p in points):.0f}")
        cards["braking"].set_value(
            f"{max((z.average_deceleration_g for z in zones), default=0.0):.2f}"
        )
        cards["events"].set_value(
            str(len(events)),
            palette.yellow if events else palette.green,
        )

        self._charts[0].set_series(
            [
                Series(
                    "vel",
                    palette.channel_speed,
                    [(self._x_of(p), p.speed_kmh) for p in points],
                )
            ]
        )
        self._charts[1].set_series(
            [
                Series(
                    "acel",
                    palette.channel_throttle,
                    [(self._x_of(p), p.throttle) for p in points],
                ),
                Series(
                    "freio",
                    palette.channel_brake,
                    [(self._x_of(p), p.brake) for p in points],
                ),
            ]
        )
        # Um mapa distância → eixo ativo, construído **uma vez**. As séries
        # derivadas (guinada, aderência, auxílios) nascem indexadas por
        # distância; converter cada ponto chamando `_x_at_distance` custaria uma
        # varredura da volta inteira por ponto, que é a repintura quadrática que
        # já travou esta página por 800 ms uma vez.
        x_at = {p.distance_m: self._x_of(p) for p in points}

        self._charts[CHART_YAW].set_series(
            [
                Series(
                    "guinada",
                    palette.channel_steering,
                    [
                        (x_at.get(distancia, distancia), graus)
                        for distancia, graus in yaw_rate_series(points)
                    ],
                )
            ]
        )
        self._fill_grip_chart(points, x_at)
        self._fill_height_chart(points, x_at)
        self._fill_boost_chart(points, x_at)
        self._fill_slope_chart(points, x_at)
        self._fill_aid_band(points, x_at)

        # A volta inteira vira nuvem no círculo de atrito. G por distância
        # respondia "quanto de G houve no metro 1.200", que não é pergunta que
        # alguém faça; o envelope bidimensional mostra como a aderência
        # disponível foi repartida entre frear, acelerar e curvar.
        self._gforce.set_points(
            [(p.g_lateral, p.g_longitudinal) for p in points]
        )
        self._gforce.set_current(None)

        # Sem cursor, a temperatura mostrada é a média da volta — o resumo. Ao
        # passar o mouse ela vira a do ponto, e é aí que se vê o pneu esquentando
        # numa sequência de curvas do mesmo lado.
        self._show_average_temperatures(points)

        # Os ápices são marcados em **distância**; no eixo de tempo eles
        # precisam virar o instante correspondente, senão a marca "C2" aparece
        # num ponto do gráfico onde o carro nem estava.
        apex_marks = [
            (self._x_at_distance(corner.apex_distance_m), f"C{corner.index}",
             palette.text_muted)
            for corner in self._corners
        ]
        for chart in self._charts:
            chart.set_markers(apex_marks)

        self._fill_map(points)

        markers = [
            TrackMarker(
                x=apex.position_x,
                z=apex.position_z,
                color=palette.purple,
                label=f"C{corner.index}",
                hollow=True,
            )
            for corner in self._corners
            if (apex := _point_at(points, corner.apex_distance_m)) is not None
        ]
        # Limites de setor: os mesmos cortes por distância que o histórico usa,
        # para que "setor 2" signifique o mesmo pedaço de asfalto nas duas telas.
        for number, boundary in enumerate(
            sector_boundaries_m(points[-1].distance_m, NUM_SECTORS)[:-1], start=1
        ):
            edge = _point_at(points, boundary)
            if edge is not None:
                markers.append(
                    TrackMarker(
                        x=edge.position_x,
                        z=edge.position_z,
                        color=palette.text_muted,
                        label=f"S{number}",
                        radius=3.0,
                    )
                )
        self._map.set_markers(markers)
        # Sem esta linha, "C3" e "S1" sobre o traçado são duas bolinhas de cores
        # diferentes e nada diz que uma é curva e a outra é divisa de setor.
        self._map.set_marker_legend(
            [(palette.purple, "curva (ápice)"), (palette.text_muted, "setor")]
        )

        self._fill_table(zones)

        balance = temperature_balance(points)
        self._tyres.setText(f"Pneus: {balance.describe()}" if balance else "")

    def _fill_grip_chart(
        self, points: list[TelemetryPoint], x_at: dict[float, float]
    ) -> None:
        """Uma série por roda, em % da velocidade do carro.

        100% é a roda girando exatamente com o carro. Abaixo de 92% ela está
        travando sob freio; acima de 108%, patinando. O gráfico de acelerador e
        freio não mostra nada disso — lá as duas situações aparecem como pedal
        no fundo, que é justamente por que este canal existe.

        Amostras abaixo do limiar de velocidade viram **lacuna**, não zero: a
        razão ali é numericamente instável, e um zero desenharia travamento
        total onde só houve divisão por um número pequeno.
        """
        palette = self.theme.palette
        cores = {
            "fl": palette.channel_speed,
            "fr": palette.green,
            "rl": palette.orange,
            "rr": palette.purple,
        }
        convencao = infer_slip_convention(points)

        series = []
        for roda in WHEELS:
            valores = [
                (x_at.get(p.distance_m, p.distance_m), razao * 100.0)
                for p in points
                if (razao := slip_ratio(p, roda, convencao)) is not None
            ]
            series.append(Series(WHEEL_LABELS[roda], cores[roda], valores))
        self._charts[CHART_GRIP].set_series(series)
        self._warn_if_slip_implausible(series, points)

    def _fill_height_chart(
        self, points: list[TelemetryPoint], x_at: dict[float, float]
    ) -> None:
        """Altura de suspensão das quatro rodas, em milímetros.

        O pacote traz esta altura em metros, num campo por roda (0xC4–0xD4). A
        conversão para milímetro é só de unidade: em metro, a variação inteira
        de uma volta cabe em três casas decimais e o eixo fica ilegível.

        Some quando o carro não mexe — o gerador sintético emitia altura
        constante até esta versão, e uma linha reta perfeita ocupando 120 px é
        um quadro que não responde nada. As mesmas cores das rodas do canal de
        aderência, para que "DE" signifique a mesma roda nos dois.
        """
        palette = self.theme.palette
        cores = {
            "fl": palette.channel_speed,
            "fr": palette.green,
            "rl": palette.orange,
            "rr": palette.purple,
        }

        series = []
        extremos: list[float] = []
        for roda in WHEELS:
            valores = [
                (x_at.get(p.distance_m, p.distance_m),
                 getattr(p, f"suspension_{roda}") * 1000.0)
                for p in points
                if getattr(p, f"suspension_{roda}") is not None
            ]
            extremos.extend(v for _, v in valores)
            series.append(Series(WHEEL_LABELS[roda], cores[roda], valores))

        mexeu = bool(extremos) and (max(extremos) - min(extremos)) >= MIN_HEIGHT_TRAVEL_MM
        self._charts[CHART_HEIGHT].setVisible(mexeu)
        if not mexeu:
            self._charts[CHART_HEIGHT].clear()
            return
        self._charts[CHART_HEIGHT].set_series(series)

    def _warn_if_slip_implausible(
        self, series: list[Series], points: list[TelemetryPoint]
    ) -> None:
        """Avisa quando o canal de escorregamento não parece ser o que se supõe.

        Numa volta inteira, as quatro rodas passam a esmagadora maioria do tempo
        rodando limpas: a média tem de ficar perto de 100%. Uma média muito
        longe disso não é pilotagem ruim — é o canal não estar chegando como o
        código presume. O campo `tire_slip_*` não tem especificação oficial, e o
        offset de onde ele é lido veio de engenharia reversa; um offset errado
        entrega um número que o gráfico desenha com toda a confiança do mundo.

        O aviso é a diferença entre um gráfico errado e um gráfico que avisa que
        pode estar errado. Foi exatamente esse tipo de silêncio que deixou toda
        volta de PS5 real ser gravada com distância 0,0 m sem ninguém notar.
        """
        valores = [v for s in series for _, v in s.points]
        if not valores:
            self._grip_hint.setText("")
            return

        media = sum(valores) / len(valores)
        if 60.0 <= media <= 160.0:
            self._grip_hint.setText("")
            return

        if _gravada_antes_da_correcao(points):
            self._grip_hint.setText(
                "Esta volta foi gravada antes da correção do canal de "
                "aderência: o valor guardado é zero e não há como recuperá-lo, "
                "porque o pacote bruto não é armazenado — só o ponto já "
                "derivado dele. Grave uma volta nova e o gráfico funciona. As "
                "voltas antigas continuam válidas em todo o resto."
            )
            return

        self._grip_hint.setText(
            f"Aderência média da volta: {media:.0f}% — implausível. As quatro "
            "rodas passam quase toda a volta perto de 100%, então este canal "
            "provavelmente não está chegando como o código presume (o campo "
            "`tire_slip` do GT7 não tem especificação oficial). Trate este "
            "gráfico e a contagem de travamentos como não confiáveis até "
            "conferir."
        )

    def _fill_map(self, points: list[TelemetryPoint]) -> None:
        """Desenha o traçado no canal escolhido.

        Velocidade é gradiente — a pergunta é "quanto". Pedais é categórico — a
        pergunta é "qual", e ver **onde na pista** o pé estava em cada um
        responde de olho o que a tabela de curvas responde em números: onde a
        freada começa, quanto dura o trecho sem pedal nenhum (que é tempo
        perdido) e onde o acelerador volta.
        """
        palette = self.theme.palette
        canal = self._map_channel.currentData()
        coordenadas = [(p.position_x, p.position_z) for p in points]
        distancias = [p.distance_m for p in points]

        if canal == "pedals":
            self._map.set_paths(
                [
                    TrackPath(
                        "traçado",
                        palette.accent,
                        coordenadas,
                        colors=[self._pedal_color(p) for p in points],
                        distances=distancias,
                    )
                ]
            )
            self._map.set_legend(
                [
                    (palette.channel_throttle, "acelerador"),
                    (palette.channel_brake, "freio"),
                    (palette.yellow, "nenhum"),
                ]
            )
            return

        self._map.set_legend([])
        self._map.set_paths(
            [
                TrackPath(
                    "traçado",
                    palette.accent,
                    coordenadas,
                    values=[p.speed_kmh for p in points],
                    distances=distancias,
                )
            ]
        )

    def _pedal_color(self, point: TelemetryPoint) -> str:
        """Verde acelerando, vermelho freando, amarelo sem pedal nenhum.

        O freio ganha quando os dois estão acionados. Não é desempate
        arbitrário: pé nos dois é *trail braking*, e o que interessa marcar no
        mapa é até onde a freada se estendeu — pintar de verde esconderia
        justamente a sobreposição que se quer enxergar.
        """
        palette = self.theme.palette
        if point.brake > PEDAL_ON_PCT:
            return palette.channel_brake
        if point.throttle > PEDAL_ON_PCT:
            return palette.channel_throttle
        return palette.yellow

    def _on_map_channel(self, _index: int) -> None:
        if self._points:
            self._fill_map(self._points)

    def _fill_boost_chart(
        self, points: list[TelemetryPoint], x_at: dict[float, float]
    ) -> None:
        """Pressão de sobrealimentação ao longo da volta.

        Lida junto com o acelerador, ela mostra o **atraso do turbo**: a
        distância entre o pedal ir ao fundo e a pressão chegar. É onde se decide
        se vale trocar o ponto de troca de marcha, e o gráfico de acelerador
        sozinho não diz nada disso — lá o pedal já está em 100%.
        """
        palette = self.theme.palette

        # Carro aspirado: o quadro seria uma reta no zero ocupando 110 px de uma
        # página já longa — um gráfico que não responde nada. Some inteiro, e a
        # linha de texto no lugar existe para a ausência não virar "cadê o
        # gráfico que estava aqui?".
        pico = max((p.boost_bar for p in points), default=0.0)
        tem_turbo = pico >= BOOST_PRESENT_BAR

        self._charts[CHART_BOOST].setVisible(tem_turbo)
        self._boost_hint.setText(
            "" if tem_turbo else "Sem turbo nesta volta — o carro é aspirado."
        )

        if not tem_turbo:
            self._charts[CHART_BOOST].clear()
            return

        self._charts[CHART_BOOST].set_series(
            [
                Series(
                    "turbo",
                    palette.orange,
                    [(x_at.get(p.distance_m, p.distance_m), p.boost_bar) for p in points],
                )
            ]
        )

    def _fill_slope_chart(
        self, points: list[TelemetryPoint], x_at: dict[float, float]
    ) -> None:
        """Rampa da pista ao longo da volta, ou o motivo de não haver rampa.

        Lida junto com a freada, é o que separa "freou fraco" de "freou numa
        descida": nos dois casos o carro desacelera menos, e só um deles é
        erro do piloto. A força G desta página já desconta a gravidade — este
        gráfico é onde se vê **de onde** veio o desconto.

        Volta gravada antes de a normal do asfalto ser lida não tem rampa
        nenhuma, e aí o quadro some inteiro em vez de desenhar uma reta no
        zero. Reta no zero afirmaria pista plana, que é dado inventado.
        """
        rampas = slope_series(points)
        medidas = [
            (x_at.get(p.distance_m, p.distance_m), valor)
            for p, valor in zip(points, rampas, strict=True)
            if valor is not None
        ]

        tem_relevo = len(medidas) >= len(points) * MIN_SLOPE_COVERAGE
        self._charts[CHART_SLOPE].setVisible(tem_relevo)

        if not tem_relevo:
            self._charts[CHART_SLOPE].clear()
            self._slope_hint.setText(
                "Sem relevo nesta volta — ela foi gravada antes de o programa "
                "ler a inclinação da pista. As voltas novas já trazem."
                if not any(p.has_road_normal for p in points)
                else "Relevo medido em pedaços pequenos demais para desenhar."
            )
            return

        self._slope_hint.setText("")
        desnivel = elevation_range_m(points)
        titulo = "Inclinação da pista  (+ subida)"
        if desnivel is not None and desnivel >= MIN_INTERESTING_ELEVATION_M:
            titulo = f"{titulo}  —  desnível de {desnivel:.0f} m na volta"
        self._charts[CHART_SLOPE].set_title(titulo)

        self._charts[CHART_SLOPE].set_series(
            [Series("inclinação", self.theme.palette.channel_slope, medidas)]
        )

    def _fill_aid_band(
        self, points: list[TelemetryPoint], x_at: dict[float, float]
    ) -> None:
        """Faixas de TCS e ASM, ou o motivo de não haver faixa nenhuma."""
        if not was_recorded(points):
            # Faixa vazia afirmaria "nenhum auxílio atuou" sobre uma volta em
            # que ninguém observou os auxílios. Dizer isso em texto é a única
            # saída honesta.
            self._aid_band.set_note(
                "auxílios não gravados nesta volta — só voltas novas trazem o dado"
            )
            return

        self._aid_band.set_note("")
        spans = {
            aid: [
                (
                    x_at.get(t.start_distance_m, t.start_distance_m),
                    x_at.get(t.end_distance_m, t.end_distance_m),
                )
                for t in aid_spans(points, aid)
            ]
            for aid in AIDS
        }
        self._aid_band.set_spans(
            spans, x_range=(self._x_of(points[0]), self._x_of(points[-1]))
        )

        self._report_abs(unknown_bits(points))

    def _report_abs(self, bits: int) -> None:
        """Diz o estado do ABS **em toda volta**, e não só quando há bit novo.

        Silêncio aqui se lê como "o ABS não atuou", que é uma afirmação que
        ninguém mediu — o bit dele não está identificado na engenharia reversa
        do pacote. Dizer isso toda vez custa uma linha e evita a pergunta
        "cadê o ABS?" voltar a cada sessão.

        Quando aparecem bits sem nome, eles são candidatos: freie forte com ABS
        ligado, depois com ele desligado, e compare as duas voltas. O que
        aparecer só na primeira é o bit — e vira fato depois de repetir, não no
        primeiro palpite.
        """
        if not bits:
            self._flags_hint.setText(
                "ABS: não há indicador. O campo de estado do pacote do GT7 tem "
                "doze bits nomeados pela engenharia reversa e nenhum deles é o "
                "ABS; nesta volta também não apareceu nenhum bit desconhecido. "
                "A faixa acima mostra os dois auxílios que o pacote nomeia: TCS "
                "(controle de tração, segura a roda que patina na aceleração) e "
                "ASM — Active Stability Management, o controle de estabilidade, "
                "que corta motor e freia rodas para conter a derrapagem. Nenhum "
                "dos dois é o ABS."
            )
            return

        posicoes = [str(i) for i in range(16) if bits & (1 << i)]
        self._flags_hint.setText(
            f"ABS: candidatos encontrados — bits sem nome nesta volta: "
            f"{', '.join(posicoes)}. Freie forte com o ABS ligado, depois com "
            "ele desligado, e compare: o bit que aparecer só na primeira é ele."
        )

    def _show_average_temperatures(self, points: list[TelemetryPoint]) -> None:
        total = len(points)
        self._tyre_temps.set_temperatures(
            sum(p.tire_temp_fl for p in points) / total,
            sum(p.tire_temp_fr for p in points) / total,
            sum(p.tire_temp_rl for p in points) / total,
            sum(p.tire_temp_rr for p in points) / total,
        )
        self._tyre_caption.setText("média da volta")

    def _fill_table(self, zones: list[BrakingZone]) -> None:
        self._table.setRowCount(len(self._corners))

        for row, corner in enumerate(self._corners):
            zone = min(
                (z for z in zones if z.start_distance_m <= corner.apex_distance_m),
                key=lambda z: corner.apex_distance_m - z.start_distance_m,
                default=None,
            )
            values = [
                f"{corner.index}  ({corner.severity})",
                f"{corner.apex_distance_m:.0f} m",
                f"{corner.minimum_speed_kmh:.1f} km/h",
                f"{zone.average_deceleration_g:.2f} g" if zone else "—",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._table.setItem(row, column, item)

    # ---------- interação ----------

    def _on_hover(self, x_value: float) -> None:
        """O valor recebido está na unidade do eixo ativo — metros ou segundos.

        O mapa da pista, porém, sempre pensa em distância: ele desenha o traçado
        e não sabe nada de tempo. Por isso a conversão acontece aqui, no ponto
        onde as duas leituras se encontram.
        """
        if self._frozen:
            return
        self._move_cursor(x_value)

    def _on_click(self, x_value: float) -> None:
        """Clique trava o cursor no ponto — e um segundo clique destrava.

        Ler os números do cursor exigia manter a mão parada em cima do gráfico,
        o que impede olhar a tabela de curvas, o mapa ou a temperatura sem
        perder a leitura. Travar resolve isso; travar **sem sinal visual** só
        faria a aplicação parecer congelada, e por isso o cursor muda de cor.
        """
        self._frozen = not self._frozen
        for chart in self._charts:
            chart.set_cursor_locked(self._frozen)
        if self._frozen:
            self._move_cursor(x_value)
        self._hint_frozen()

    def _hint_frozen(self) -> None:
        """Rótulo próprio, e não o dos bits de estado.

        Escrito no rótulo dos bits, este aviso apagava a linha do ABS a cada
        clique — o mesmo defeito que já tinha acontecido com o rótulo de pneus,
        agora entre duas mensagens que nada têm a ver uma com a outra.
        """
        self._cursor_hint.setText(
            "Cursor travado — clique de novo para soltar." if self._frozen else ""
        )

    def _move_cursor(self, x_value: float) -> None:
        for chart in self._charts:
            chart.set_cursor(x_value)

        self._aid_band.set_cursor(x_value)

        point = self._point_at_x(x_value)
        if point is None:
            self._map.set_cursor(None)
            return
        self._map.set_cursor(point.distance_m)

        self._tyre_temps.set_temperatures(
            point.tire_temp_fl,
            point.tire_temp_fr,
            point.tire_temp_rl,
            point.tire_temp_rr,
        )
        atuando = self._aid_band.active_at(x_value)
        self._tyre_caption.setText(
            f"no cursor · {' + '.join(atuando)} atuando" if atuando else "no cursor"
        )

        rows = self._detail_rows
        rows["distance"].set_value(f"{point.distance_m:.0f} m")
        rows["speed"].set_value(f"{point.speed_kmh:.1f} km/h")
        rows["throttle"].set_value(f"{point.throttle:.0f} %")
        rows["brake"].set_value(f"{point.brake:.0f} %")
        rows["gear"].set_value(str(point.gear) if point.gear > 0 else "N")
        # A bola liga o ponto da pista sob o cursor ao lugar dele no envelope
        # de aderência — é o que transforma a nuvem em leitura, em vez de
        # decoração.
        self._gforce.set_current((point.g_lateral, point.g_longitudinal))

        # A curva é indexada por distância, sempre — o eixo pode estar em
        # tempo, mas a pista não muda de forma por causa disso.
        corner = corner_at(self._corners, point.distance_m)
        rows["corner"].set_value(
            f"{corner.index} ({corner.severity})" if corner else "—"
        )

    def _x_at_distance(self, distance_m: float) -> float:
        """Converte uma distância para a unidade do eixo em uso."""
        if self._x_mode != "time":
            return distance_m
        point = _point_at(self._points, distance_m)
        return point.elapsed_ms / 1000.0 if point else distance_m

    def _point_at_x(self, x_value: float) -> TelemetryPoint | None:
        """Amostra sob o cursor, na unidade do eixo ativo.

        Sem isto, no modo tempo o cursor procuraria "a amostra a 42 metros"
        recebendo 42 **segundos** — e apontaria para o começo da volta enquanto
        o mouse está no fim.
        """
        if not self._points:
            return None
        if self._x_mode != "time":
            return _point_at(self._points, x_value)
        alvo_ms = x_value * 1000.0
        return min(self._points, key=lambda p: abs(p.elapsed_ms - alvo_ms))

    def _x_of(self, point: TelemetryPoint) -> float:
        """Distância ou tempo, conforme o seletor.

        Por distância, duas voltas se sobrepõem no mesmo ponto da pista mesmo
        com ritmos diferentes — é o que permite ver *onde* a diferença nasce.
        Por tempo, vê-se quanto cada trecho custou em segundos, que a distância
        comprime justamente onde o carro está devagar.
        """
        return point.elapsed_ms / 1000.0 if self._x_mode == "time" else point.distance_m

    def _on_x_mode(self, index: int) -> None:
        self._x_mode = "time" if index == 1 else "distance"
        for chart in self._charts:
            chart.set_x_unit("s" if self._x_mode == "time" else "m")
        if self._points:
            self._populate(self._lap_time_ms)

    def _on_g_scale(self, index: int) -> None:
        """Automático é o primeiro item, e devolve `None` — que é o que
        `set_scale` entende como "escolha o menor degrau que couber"."""
        self._gforce.set_scale(self._g_scale.itemData(index))

    def _on_hover_left(self) -> None:
        if self._frozen:
            return
        for chart in self._charts:
            chart.set_cursor(None)
        self._map.set_cursor(None)
        self._gforce.set_current(None)
        self._aid_band.set_cursor(None)
        # Volta para o resumo da volta em vez de congelar no último ponto
        # apontado, que ficaria parecendo o estado atual do carro.
        if self._points:
            self._show_average_temperatures(self._points)

    def _on_row_selected(self) -> None:
        """Selecionar uma curva na tabela move o cursor dos gráficos até ela."""
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if 0 <= index < len(self._corners):
            # A tabela guarda distância; o cursor fala a unidade do eixo.
            # `_move_cursor` e não `_on_hover`: escolher uma curva na tabela é um
            # comando explícito, e com o cursor travado `_on_hover` o ignoraria
            # — a tabela pareceria ter parado de funcionar.
            self._move_cursor(
                self._x_at_distance(self._corners[index].apex_distance_m)
            )


def _gravada_antes_da_correcao(points: list[TelemetryPoint]) -> bool:
    """A volta foi gravada com o offset errado do canal de escorregamento?

    A assinatura é inequívoca: as quatro rodas **exatamente** 0,0 com o carro
    andando. O offset antigo (0xE4) caía no bloco não usado do pacote, que vem
    zerado; o atual lê rotação e raio, e roda que gira não dá zero exato.

    A distinção importa porque as duas situações pedem coisas opostas. Volta
    antiga: o dado não existe mais e nunca vai existir — o pacote bruto não é
    guardado, só o ponto derivado dele —, então a saída é gravar uma volta nova.
    Canal genuinamente errado: é defeito a investigar. Um aviso só, dizendo
    "provavelmente não está chegando", manda procurar um defeito já corrigido.
    """
    andando = [p for p in points if p.speed_kmh >= MIN_SPEED_FOR_SLIP_KMH]
    if not andando:
        return False
    return all(
        getattr(p, f"tire_slip_{roda}") == 0.0
        for p in andando
        for roda in WHEELS
    )


def _point_at(
    points: list[TelemetryPoint], distance_m: float
) -> TelemetryPoint | None:
    """Amostra mais próxima da distância informada."""
    if not points:
        return None
    return min(points, key=lambda p: abs(p.distance_m - distance_m))
