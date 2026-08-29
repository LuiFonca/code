"""
Página de comparação — duas voltas, lado a lado, e a conta de onde saiu a
diferença.

A pergunta que esta página responde é a do §20: *onde estou perdendo tempo?*
O `TimeLossReport` da Fase 4 já a responde em texto; aqui ela vira tela.

A ordenação da tabela é por perda, não por posição na pista. Um relatório
ordenado por distância obriga o piloto a ler tudo para achar o que importa;
ordenado por perda, a primeira linha já é o que treinar amanhã.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gt7core.analytics.aids import aid_spans, was_recorded
from gt7core.analytics.corners import detect_corners
from gt7core.analytics.elevation import slope_series
from gt7core.analytics.series import (
    LapSeries,
    compute_delta_series,
    sector_boundaries_m,
)
from gt7core.analytics.timeloss import TimeLossReport, analyse_time_loss
from gt7core.domain.models import TelemetryPoint

from ..application import CoreApplication
from ..design.tokens import Space, Theme
from ..pages.analysis import (
    BOOST_PRESENT_BAR,
    BOOST_STEP_BAR,
    BOOST_TOP_MIN_BAR,
    MIN_SLOPE_COVERAGE,
    NUM_SECTORS,
    SLOPE_STEP_PCT,
    SLOPE_TOP_MIN_PCT,
)
from ..widgets.advice import AdviceCard
from ..widgets.aidband import AidBand
from ..widgets.cards import Card, MetricCard, MetricGrid
from ..widgets.charts import (
    SPEED_STEP_KMH,
    SPEED_TOP_MIN_KMH,
    DistanceChart,
    Series,
)
from ..widgets.selectors import TrackLapSelector
from ..widgets.trackmap import TrackMap, TrackMarker, TrackPath
from .base import Page

SEGMENT_COLUMNS = ("Trecho", "Início", "Δ tempo", "Diagnóstico")

#: Linhas da faixa de auxílios: um auxílio por volta.
#:
#: O ABS não está aqui porque **o bit dele não está identificado** na engenharia
#: reversa do pacote — ver `gt7core.analytics.aids`. Inventar uma linha "ABS"
#: alimentada por um bit chutado seria pior que não ter: ela pareceria medida.
COMPARE_AID_ROWS = ("TCS ref.", "TCS comp.", "ASM ref.", "ASM comp.")

#: Rótulo curto de cada linha. A calha da faixa tem 46 px — a largura da margem
#: esquerda dos gráficos, que ela precisa respeitar para alinhar com o eixo X.
#: "TCS comp." não cabe e saía truncado em "; comp."; a linha diz o auxílio e a
#: **cor** diz a volta, que já é a convenção do resto da página.
AID_ROW_LABELS = {
    "TCS ref.": "TCS",
    "TCS comp.": "TCS",
    "ASM ref.": "ASM",
    "ASM comp.": "ASM",
}

#: Da linha da faixa para o auxílio de verdade.
AID_OF_ROW = {
    "TCS ref.": "TCS",
    "TCS comp.": "TCS",
    "ASM ref.": "ASM",
    "ASM comp.": "ASM",
}

#: Quais linhas pertencem à volta comparada (o resto é a referência).
COMPARED_ROWS = frozenset({"TCS comp.", "ASM comp."})

#: Linhas do cartão do cursor: (chave, rótulo, unidade).
CURSOR_ROWS = (
    ("speed", "Velocidade", "km/h"),
    ("throttle", "Acelerador", "%"),
    ("brake", "Freio", "%"),
    ("gear", "Marcha", ""),
)


class LapColors:
    """As duas cores da página, num lugar só.

    Eram roxo e azul, e roxo e azul são vizinhos: sobrepostos no mapa, os dois
    traçados viravam uma linha só e a comparação de linha de corrida — que é a
    razão de o mapa existir aqui — ficava ilegível.

    Amarelo contra azul é o par de maior separação que a paleta oferece: difere
    em matiz **e** em luminosidade, então sobrevive à sobreposição e também a
    quem não distingue vermelho de verde.

    A regra que importa é a consistência: a mesma cor significa a mesma volta no
    mapa, nos cinco gráficos, na faixa de auxílios e na legenda. Duas definições
    em arquivos diferentes é como o mapa acabaria dizendo que amarelo é a
    referência enquanto o gráfico diz que é a comparada.
    """

    def __init__(self, theme: Theme) -> None:
        self.reference = theme.palette.yellow
        self.compared = theme.palette.channel_speed


class CursorComparison(QWidget):
    """Três colunas por canal: referência, comparada e a diferença.

    A página já mostrava as duas curvas sobrepostas, o que responde "onde elas
    divergem" mas não "por quanto" — para isso o olho tem de medir a distância
    entre dois traços contra um eixo, que é justamente o que o olho não faz bem.
    A terceira coluna faz a subtração, que é a leitura que interessa: 8 km/h a
    menos na saída da curva 4 é um número acionável; dois traços quase juntos
    não são.

    A diferença é sempre **comparada − referência**, e a ordem está escrita no
    cabeçalho. Sem isso, um `−8` seria ambíguo entre "perdeu 8" e "ganhou 8", e
    o cartão inteiro viraria adivinhação.
    """

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._values: dict[str, tuple[QLabel, QLabel, QLabel]] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(Space.XS.px)

        for column, titulo in enumerate(("", "referência", "comparada", "dif.")):
            cabecalho = QLabel(titulo)
            cabecalho.setProperty("role", "muted")
            if column:
                cabecalho.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(cabecalho, 0, column)

        for row, (key, rotulo, unidade) in enumerate(CURSOR_ROWS, start=1):
            nome = QLabel(rotulo)
            nome.setProperty("role", "muted")
            grid.addWidget(nome, row, 0)

            celulas = []
            for column in (1, 2, 3):
                celula = QLabel("—")
                celula.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(celula, row, column)
                celulas.append(celula)
            self._values[key] = (celulas[0], celulas[1], celulas[2])
            del unidade

        grid.setColumnStretch(0, 2)
        for column in (1, 2, 3):
            grid.setColumnStretch(column, 1)

    def set_values(
        self,
        reference: TelemetryPoint,
        analysed: TelemetryPoint,
    ) -> None:
        palette = self._theme.palette

        def marcha(point: TelemetryPoint) -> str:
            return str(point.gear) if point.gear > 0 else "N"

        leituras = {
            "speed": (
                f"{reference.speed_kmh:.1f}",
                f"{analysed.speed_kmh:.1f}",
                analysed.speed_kmh - reference.speed_kmh,
            ),
            "throttle": (
                f"{reference.throttle:.0f}",
                f"{analysed.throttle:.0f}",
                analysed.throttle - reference.throttle,
            ),
            "brake": (
                f"{reference.brake:.0f}",
                f"{analysed.brake:.0f}",
                analysed.brake - reference.brake,
            ),
            "gear": (marcha(reference), marcha(analysed), analysed.gear - reference.gear),
        }

        for key, (ref_texto, ana_texto, diferenca) in leituras.items():
            ref_label, ana_label, dif_label = self._values[key]
            ref_label.setText(ref_texto)
            ana_label.setText(ana_texto)
            dif_label.setText(f"{diferenca:+.1f}" if key != "gear" else f"{diferenca:+d}")
            # Verde quando a volta comparada está por cima **naquele canal**.
            # Mais freio não é melhor nem pior sem contexto, então o freio fica
            # neutro em vez de fingir um julgamento.
            if key in ("speed", "throttle"):
                cor = palette.green if diferenca > 0 else palette.yellow
                dif_label.setStyleSheet(f"color: {cor};" if diferenca else "")
            else:
                dif_label.setStyleSheet("")

    def clear(self) -> None:
        for celulas in self._values.values():
            for celula in celulas:
                celula.setText("—")
                celula.setStyleSheet("")


class ComparePage(Page):
    page_id = "compare"
    nav_title = "Comparar"
    title = "Comparação de voltas"
    subtitle = "Onde a diferença foi feita"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        self._reference: list[TelemetryPoint] = []
        self._analysed: list[TelemetryPoint] = []
        #: Cursor travado por clique — ver a nota igual na página de análise.
        self._frozen = False
        self._colors = LapColors(theme)
        super().__init__(core, theme)

    def _aid_row_colors(self) -> dict[str, str]:
        return {
            row: (self._colors.compared if row in COMPARED_ROWS else self._colors.reference)
            for row in COMPARE_AID_ROWS
        }

    # ---------- construção ----------

    def build(self) -> None:
        selectors = QVBoxLayout()
        selectors.setSpacing(Space.XS.px)

        self._reference_selector = TrackLapSelector(
            self.core.tracks, self.core.laps, lap_label="Referência:"
        )
        self._analysed_selector = TrackLapSelector(
            self.core.tracks,
            self.core.laps,
            lap_label="Comparar:",
            show_track=False,
        )
        self._reference_selector.lap_changed.connect(self._on_selection)
        self._analysed_selector.lap_changed.connect(self._on_selection)
        # A segunda volta segue a pista da primeira, sempre.
        self._reference_selector.track_changed.connect(
            self._analysed_selector.select_track
        )

        selectors.addWidget(self._reference_selector)
        selectors.addWidget(self._analysed_selector)

        self._car_hint = QLabel("")
        self._car_hint.setWordWrap(True)
        selectors.addWidget(self._car_hint)

        holder = QVBoxLayout()
        holder.addLayout(selectors)
        wrapper = Card()
        wrapper.body().addLayout(holder)
        self.header.add_action(wrapper)

        self._summary = MetricGrid(columns=4)
        for key, label, unit in (
            ("total", "Diferença total", "s"),
            ("recoverable", "Recuperável", "s"),
            ("worst", "Pior trecho", ""),
            ("segments", "Trechos perdidos", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        middle = QHBoxLayout()
        middle.setSpacing(Space.LG.px)

        charts = Card("Canais por distância")
        self._delta_chart = DistanceChart(
            self.theme, "Delta acumulado", unit="s", height=140
        )
        self._speed_chart = DistanceChart(
            self.theme,
            "Velocidade",
            unit="km/h",
            height=140,
            y_step=SPEED_STEP_KMH,
            y_top_min=SPEED_TOP_MIN_KMH,
        )
        # Acelerador e freio em gráficos **separados**, e não os quatro traços
        # num só. Sobrepostos, dois aceleradores e dois freios viram um emaranhado
        # em que não se lê nem qual volta nem qual pedal; separados, cada quadro
        # tem só duas linhas e a diferença entre elas é a informação inteira.
        self._throttle_chart = DistanceChart(
            self.theme, "Acelerador", unit="%", height=120, y_range=(0.0, 100.0)
        )
        self._brake_chart = DistanceChart(
            self.theme, "Freio", unit="%", height=120, y_range=(0.0, 100.0)
        )
        self._boost_chart = DistanceChart(
            self.theme,
            "Pressão de turbo",
            unit="bar",
            height=120,
            y_step=BOOST_STEP_BAR,
            y_top_min=BOOST_TOP_MIN_BAR,
        )
        # Inclinação da pista: **uma** linha, e não o par.
        #
        # A comparação só aceita voltas da mesma pista, então a rampa é a mesma
        # estrada nas duas — desenhar duas linhas praticamente idênticas
        # convidaria a procurar diferença onde não pode haver. Aqui ela é
        # contexto: é o quadro que responde "por que perdi dois décimos neste
        # trecho sem ter errado nada", quando a resposta é que ali sobe.
        self._slope_chart = DistanceChart(
            self.theme,
            "Inclinação da pista  (+ subida)",
            unit="%",
            height=110,
            y_step=SLOPE_STEP_PCT,
            y_top_min=SLOPE_TOP_MIN_PCT,
            y_symmetric=True,
        )

        self._charts = [
            self._delta_chart,
            self._speed_chart,
            self._throttle_chart,
            self._brake_chart,
            self._boost_chart,
            self._slope_chart,
        ]
        for chart in self._charts:
            charts.add(chart)
            chart.hovered.connect(self._on_hover)
            chart.clicked.connect(self._on_click)

        # Quatro linhas: dois auxílios × duas voltas. A cor identifica a volta,
        # e não o auxílio — a pergunta aqui é qual das duas pediu mais ajuda ao
        # computador, e onde.
        self._aid_band = AidBand(
            self.theme,
            aids=COMPARE_AID_ROWS,
            colors=self._aid_row_colors(),
            labels=AID_ROW_LABELS,
        )
        charts.add(self._aid_band)

        self._aid_hint = QLabel("")
        self._aid_hint.setWordWrap(True)
        charts.add(self._aid_hint)

        middle.addWidget(charts, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(Space.LG.px)

        # Mapa alto de propósito: é aqui que se compara **linha de corrida**,
        # e duas linhas separadas por meio metro só se distinguem com área. O
        # widget reamostra proporcional ao próprio tamanho, então dar espaço
        # aumenta a fidelidade além do tamanho aparente.
        map_card = Card("Traçados sobrepostos")
        self._map = TrackMap(self.theme, height=460)
        self._map.hovered.connect(self._on_hover)
        self._map.clicked.connect(self._on_click)
        map_card.add(self._map)
        right.addWidget(map_card)

        cursor_card = Card("No cursor")
        self._cursor_distance = QLabel("—")
        self._cursor_distance.setProperty("role", "muted")
        self._cursor_delta = QLabel("—")
        self._cursor_comparison = CursorComparison(self.theme)
        cursor_card.add(self._cursor_distance)
        cursor_card.add(self._cursor_comparison)
        cursor_card.add(self._cursor_delta)
        right.addWidget(cursor_card)
        right.addStretch(1)

        middle.addLayout(right, stretch=2)
        self.content.addLayout(middle)

        table_card = Card("Perda por trecho — do pior para o menor")
        self._table = QTableWidget(0, len(SEGMENT_COLUMNS))
        self._table.setHorizontalHeaderLabels(SEGMENT_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(200)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        table_card.add(self._table)
        self.content.addWidget(table_card)

        # O engenheiro fica **abaixo** da tabela de propósito. A tabela é o
        # fato medido; o conselho é interpretação em cima dela. Pôr a
        # interpretação primeiro convidaria a ler o texto e não conferir os
        # números — que é a inversão que este projeto inteiro tenta evitar.
        self._advice = AdviceCard(self.theme)
        self.content.addWidget(self._advice)

        service = self.core.engineer_service
        if service is None or not service.is_available:
            self._advice.show_unavailable()
        else:
            service.started.connect(self._on_engineer_started)
            service.ready.connect(self._on_advice)
            service.failed.connect(self._advice.show_error)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self.content.addWidget(self._hint)

    # ---------- dados ----------

    def refresh(self) -> None:
        self._reference_selector.reload()
        self._analysed_selector.reload()
        self._preselect_best()

    def _preselect_best(self) -> None:
        """Referência começa na melhor volta da pista — é o padrão útil.

        Sem isso, abrir a página compararia duas voltas arbitrárias e o piloto
        teria que configurar antes de ver qualquer coisa.
        """
        track_id = self._reference_selector.current_track_id()
        if track_id is None:
            return
        best = self.core.laps.get_best(track_id)
        if best is not None and best.id is not None:
            self._reference_selector.select_lap(best.id)

    def _on_selection(self, _lap_id: object) -> None:
        reference_id = self._reference_selector.current_lap_id()
        analysed_id = self._analysed_selector.current_lap_id()

        if reference_id is None or analysed_id is None:
            self._clear("escolha duas voltas para comparar")
            return
        if reference_id == analysed_id:
            self._clear("escolha voltas diferentes")
            return

        self._reference = self.core.laps.load_points(reference_id)
        self._analysed = self.core.laps.load_points(analysed_id)

        if len(self._reference) < 2 or len(self._analysed) < 2:
            self._clear("uma das voltas não tem amostras gravadas")
            return

        self._show_cars(reference_id, analysed_id)
        self._populate()

    def _show_cars(self, reference_id: int, analysed_id: int) -> None:
        """Qual carro fez cada volta — e o aviso quando não são o mesmo.

        Duas voltas da mesma pista com carros diferentes comparam bem em linha
        de corrida e em ponto de freada, e comparam mal em tudo que depende de
        potência e peso. Nada na tela dizia isso: o delta acumulado de um carro
        30 cv mais forte parece erro de pilotagem, e é do carro.
        """
        referencia = self.core.laps.get_by_id(reference_id)
        comparada = self.core.laps.get_by_id(analysed_id)
        nome_ref = self.car_name(referencia.car_id if referencia else None)
        nome_comp = self.car_name(comparada.car_id if comparada else None)

        self._reference_selector.set_car(nome_ref)
        self._analysed_selector.set_car(nome_comp)

        self._car_hint.setText(
            ""
            if nome_ref == nome_comp
            else (
                f"Carros diferentes: {nome_ref} (referência) contra {nome_comp} "
                "(comparada). Linha de corrida e ponto de freada continuam "
                "comparáveis; ritmo em reta e delta acumulado, não — parte da "
                "diferença é do carro, não da pilotagem."
            )
        )

    def _clear(self, hint: str) -> None:
        self._summary.clear_values(self.theme)
        for chart in self._charts:
            chart.clear()
            chart.set_cursor_locked(False)
        self._map.clear()
        self._aid_band.clear()
        self._aid_hint.setText("")
        self._table.setRowCount(0)
        self._cursor_comparison.clear()
        self._cursor_distance.setText("—")
        self._cursor_delta.setText("—")
        self._cursor_delta.setStyleSheet("")
        self._frozen = False
        self._hint.setText(hint)
        self._car_hint.setText("")
        self._reference_selector.set_car("")
        self._analysed_selector.set_car("")

    def _populate(self) -> None:
        palette = self.theme.palette
        report = analyse_time_loss(self._reference, self._analysed)

        cards = self._summary.cards
        total_s = report.total_delta_ms / 1000.0
        cards["total"].set_value(f"{total_s:+.3f}", palette.delta(total_s))
        cards["recoverable"].set_value(f"{report.recoverable_ms / 1000:.3f}")

        worst = report.worst(1)
        cards["worst"].set_value(worst[0].label if worst else "—")
        cards["segments"].set_value(str(len(report.losses)))

        delta_series = compute_delta_series(
            LapSeries(self._reference), LapSeries(self._analysed)
        )
        self._delta_chart.set_series(
            [Series("delta", palette.accent, delta_series)]
        )
        self._speed_chart.set_series(self._pair("speed_kmh"))
        self._throttle_chart.set_series(self._pair("throttle"))
        self._brake_chart.set_series(self._pair("brake"))
        self._fill_boost()
        self._fill_slope()
        self._fill_aid_band()

        # As marcas destacam onde se perdeu, não todas as curvas: um gráfico
        # cheio de linhas pontilhadas não destaca nada.
        #
        # A cor da marca é neutra, e não mais o amarelo: amarelo agora identifica
        # a volta de referência, e uma marca amarela no meio de traços amarelos
        # passaria a parecer parte do dado.
        self._delta_chart.set_markers(
            [
                (segment.start_distance_m, segment.label, palette.text_muted)
                for segment in report.worst(3)
            ]
        )

        self._map.set_paths(
            [
                TrackPath(
                    "referência",
                    self._colors.reference,
                    [(p.position_x, p.position_z) for p in self._reference],
                    distances=[p.distance_m for p in self._reference],
                ),
                TrackPath(
                    "comparada",
                    self._colors.compared,
                    [(p.position_x, p.position_z) for p in self._analysed],
                    dashed=True,
                    distances=[p.distance_m for p in self._analysed],
                ),
            ]
        )
        self._fill_map_markers()

        self._fill_table(report)
        self._request_debrief(report)
        self._hint.setText(
            "O tempo de cada trecho é a variação do delta dentro dele — "
            "não o delta acumulado. Por isso um trecho ruim não contamina os "
            "seguintes."
        )

    def _fill_map_markers(self) -> None:
        """Curvas e setores, os mesmos da Análise e no mesmo lugar da pista.

        Antes daqui o mapa marcava os **três piores trechos**, rotulados com o
        nome da curva mais próxima e plotados no *início* do trecho. Duas coisas
        davam errado: a bolinha caía longe do ápice que o rótulo nomeava, e o
        conjunto mudava a cada par de voltas escolhido. Ler "Curva 4" num ponto
        que não é a curva 4, e ver as marcas dançarem entre comparações, é o que
        fazia os pontos parecerem aleatórios — porque, como referência de pista,
        eles eram.

        Onde se perdeu tempo continua respondido, e melhor, por quem já
        respondia: a tabela de trechos e as marcas do gráfico de delta.
        """
        palette = self.theme.palette
        pontos = self._reference

        marcas = [
            TrackMarker(
                x=apex.position_x,
                z=apex.position_z,
                color=palette.purple,
                label=f"C{corner.index}",
                hollow=True,
            )
            for corner in detect_corners(pontos)
            if (apex := _point_at(pontos, corner.apex_distance_m)) is not None
        ]
        for numero, divisa in enumerate(
            sector_boundaries_m(pontos[-1].distance_m, NUM_SECTORS)[:-1], start=1
        ):
            borda = _point_at(pontos, divisa)
            if borda is not None:
                marcas.append(
                    TrackMarker(
                        x=borda.position_x,
                        z=borda.position_z,
                        color=palette.text_muted,
                        label=f"S{numero}",
                        radius=3.0,
                    )
                )

        self._map.set_markers(marcas)
        self._map.set_marker_legend(
            [(palette.purple, "curva (ápice)"), (palette.text_muted, "setor")]
        )

    def _pair(self, attribute: str) -> list[Series]:
        """As duas voltas no mesmo canal, sempre nesta ordem e nestas cores.

        Uma função só para os três gráficos: repetir o par a cada canal é como
        um deles acabaria com as cores trocadas, e aí a mesma cor significaria
        voltas diferentes em dois quadros vizinhos.
        """
        return [
            Series(
                "referência",
                self._colors.reference,
                [(p.distance_m, float(getattr(p, attribute))) for p in self._reference],
            ),
            Series(
                "comparada",
                self._colors.compared,
                [(p.distance_m, float(getattr(p, attribute))) for p in self._analysed],
            ),
        ]

    def _fill_boost(self) -> None:
        """Turbo só entra na tela se houver turbo.

        Num carro aspirado o quadro seria duas retas no zero ocupando 120 px de
        uma página que já é longa — um gráfico que não responde nada. Some
        inteiro, e a linha de texto no lugar existe para a ausência não virar
        "cadê o gráfico que estava aqui?".

        Basta **uma** das voltas ter turbo para o quadro aparecer: comparar o
        mesmo carro é o caso normal, mas trocar de carro entre as voltas é
        legítimo, e aí a reta no zero de um deles é justamente a comparação.
        """
        pico = max(
            (p.boost_bar for p in self._reference + self._analysed),
            default=0.0,
        )
        tem_turbo = pico >= BOOST_PRESENT_BAR

        self._boost_chart.setVisible(tem_turbo)
        if tem_turbo:
            self._boost_chart.set_series(self._pair("boost_bar"))
        else:
            self._boost_chart.clear()

    def _fill_slope(self) -> None:
        """A rampa da pista, tirada da volta de referência.

        Some inteira quando a referência não tem relevo gravado, em vez de
        desenhar uma reta no zero: reta no zero afirma pista plana, e volta
        antiga não mediu isso.
        """
        rampas = slope_series(self._reference)
        medidas = [
            (ponto.distance_m, valor)
            for ponto, valor in zip(self._reference, rampas, strict=True)
            if valor is not None
        ]
        tem_relevo = bool(self._reference) and len(medidas) >= len(
            self._reference
        ) * MIN_SLOPE_COVERAGE

        self._slope_chart.setVisible(tem_relevo)
        if not tem_relevo:
            self._slope_chart.clear()
            return
        self._slope_chart.set_series(
            [Series("pista", self.theme.palette.channel_slope, medidas)]
        )

    def _fill_aid_band(self) -> None:
        """TCS e ASM das duas voltas, quatro linhas no mesmo eixo."""
        gravado = was_recorded(self._reference) and was_recorded(self._analysed)
        if not gravado:
            self._aid_band.set_note(
                "auxílios não gravados numa das voltas — só voltas novas trazem o dado"
            )
            self._aid_hint.setText("")
            return

        self._aid_band.set_note("")
        spans: dict[str, list[tuple[float, float]]] = {}
        for row in COMPARE_AID_ROWS:
            pontos = self._analysed if row in COMPARED_ROWS else self._reference
            spans[row] = [
                (t.start_distance_m, t.end_distance_m)
                for t in aid_spans(pontos, AID_OF_ROW[row])
            ]

        fim = max(self._reference[-1].distance_m, self._analysed[-1].distance_m)
        self._aid_band.set_spans(spans, x_range=(0.0, fim))

        # O ABS não tem linha porque o bit dele não está identificado. Dizer
        # isso é diferente de omitir: sem a frase, a ausência pareceria "o ABS
        # não atuou".
        self._aid_hint.setText(
            "Duas linhas por auxílio: a de cima é a referência, a de baixo a "
            "comparada — as mesmas cores do mapa e dos gráficos. O ABS não "
            "aparece porque o bit dele não está identificado no pacote do GT7; "
            "a aba de Análise reporta bits desconhecidos, que é como achá-lo."
        )

    # ---------- engenheiro ----------

    def _request_debrief(self, report: TimeLossReport) -> None:
        """Pede o debrief da comparação exibida. Não bloqueia nada.

        O pedido sai a cada troca de volta, e é o `EngineerService` que resolve
        a corrida: pedidos que chegam com outro em andamento substituem o
        pendente, e resposta de volta que já não está na tela é descartada.
        """
        service = self.core.engineer_service
        if service is None or not service.is_available:
            return

        session = self.core.session_manager.session
        car = session.car.name if session.car else ""
        service.request_debrief(
            report,
            track=self._track_name(),
            car=car,
            lap_time_ms=self._analysed[-1].elapsed_ms if self._analysed else 0,
            reference_time_ms=(
                self._reference[-1].elapsed_ms if self._reference else None
            ),
            corners=detect_corners(self._analysed),
        )

    def _track_name(self) -> str:
        track_id = self._analysed_selector.current_track_id()
        for track in self.core.tracks.get_all():
            if track.id == track_id:
                return track.name
        return ""

    def _on_engineer_started(self, level: str) -> None:
        if level == "debrief":
            self._advice.show_thinking()

    def _on_advice(self, advice: object) -> None:
        # Só o debrief interessa a esta página: o serviço é compartilhado, e a
        # nota de rádio pedida pela página ao vivo chega aqui também.
        if str(getattr(getattr(advice, "level", ""), "value", "")) == "debrief":
            self._advice.show_advice(advice)

    def _fill_table(self, report: TimeLossReport) -> None:
        palette = self.theme.palette

        # Perdas primeiro (do pior para o menor), depois os ganhos.
        ordered = report.losses + report.gains
        self._table.setRowCount(len(ordered))

        for row, segment in enumerate(ordered):
            cells = (
                segment.label,
                f"{segment.start_distance_m:.0f} m",
                f"{segment.time_delta_ms / 1000.0:+.3f} s",
                segment.cause() or "—",
            )
            for column, text in enumerate(cells):
                self._table.setItem(row, column, QTableWidgetItem(text))

            # Só a coluna de delta é colorida: é a que carrega o julgamento, e
            # colorir a linha inteira tornaria a tabela ilegível.
            delta_item = self._table.item(row, 2)
            if delta_item is not None:
                delta_item.setForeground(
                    QColor(palette.yellow if segment.is_loss else palette.green)
                )

    def _on_click(self, distance_m: float) -> None:
        """Trava o cursor no ponto; um segundo clique solta.

        Sem isto, ler os três números do cartão exigia manter a mão parada em
        cima do gráfico — e tirar o mouse para conferir a tabela de trechos
        perdia a leitura.
        """
        self._frozen = not self._frozen
        for chart in self._charts:
            chart.set_cursor_locked(self._frozen)
        if self._frozen:
            self._move_cursor(distance_m)

    def _on_hover(self, distance_m: float) -> None:
        """Um cursor só, compartilhado pelos dois gráficos, pelo mapa e pelo
        cartão de leitura."""
        if self._frozen:
            return
        self._move_cursor(distance_m)

    def _move_cursor(self, distance_m: float) -> None:
        for chart in self._charts:
            chart.set_cursor(distance_m)
        self._map.set_cursor(distance_m)
        self._aid_band.set_cursor(distance_m)

        referencia = _point_at(self._reference, distance_m)
        comparada = _point_at(self._analysed, distance_m)
        if referencia is None or comparada is None:
            self._cursor_comparison.clear()
            return

        self._cursor_distance.setText(f"{distance_m:.0f} m")
        self._cursor_comparison.set_values(referencia, comparada)

        # O delta sai do próprio gráfico, interpolado, em vez de ser recalculado
        # aqui: dois caminhos para o mesmo número acabam discordando em algum
        # canto, e aí nada na tela diz qual dos dois está certo.
        leituras = self._delta_chart.value_at(distance_m)
        if leituras:
            segundos = leituras[0][1]
            self._cursor_delta.setText(f"delta acumulado {segundos:+.3f} s")
            self._cursor_delta.setStyleSheet(
                f"color: {self.theme.palette.delta(segundos)};"
            )
        else:
            self._cursor_delta.setText("—")

    def _on_row_selected(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        item = self._table.item(rows[0].row(), 1)
        if item is None:
            return
        try:
            distance = float(item.text().split()[0])
        except (ValueError, IndexError):
            return
        # Escolher um trecho na tabela é comando explícito: move o cursor mesmo
        # travado, senão a tabela pareceria ter parado de funcionar.
        self._move_cursor(distance)


def _point_at(
    points: list[TelemetryPoint], distance_m: float
) -> TelemetryPoint | None:
    """Amostra mais próxima da distância informada."""
    if not points:
        return None
    return min(points, key=lambda p: abs(p.distance_m - distance_m))
