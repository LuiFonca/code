"""
Página ao vivo — o painel durante a pilotagem.

Portada da `window.py` da Fase 3, agora sobre o design system e com as tiras de
telemetria em tempo real que faltavam.

Uma decisão de ritmo: o painel recebe ~60 quadros por segundo, mas repinta a
15 Hz. O olho não distingue mais que isso num número, e repintar seis cartões
mais três gráficos a 60 Hz consome CPU que a captura precisa. O ViewModel já
entrega o último quadro num timer próprio; aqui só se cuida de não fazer
trabalho extra por quadro.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from gt7core.analytics.live import RaceEventDetected
from gt7core.domain.models import Track
from gt7core.session.manager import LapSaved
from gt7core.telemetry.engine import TelemetryReceived
from gt7core.telemetry.sources.base import ConnectionState

from ..application import CoreApplication
from ..design.theme import OBJ_GHOST_BUTTON, OBJ_SELECTOR_NOTE, OBJ_STATUS_BAR
from ..design.tokens import Space, Theme
from ..viewmodels.live import LiveViewModel
from ..widgets.cards import Badge, Card, MetricCard, MetricGrid
from ..widgets.charts import (
    SPEED_STEP_KMH,
    SPEED_TOP_MIN_KMH,
    DistanceChart,
    Series,
)
from ..widgets.radio import RadioCard
from ..widgets.tyres import TyreTemperatures
from .base import Page

if TYPE_CHECKING:  # pragma: no cover - só para o verificador
    from gt7voice import VoiceRadio

#: Janela do painel ao vivo, em segundos. **Fixa**, e não "o que couber no
#: rastro": com janela elástica a escala horizontal muda a cada quadro, o
#: traço parece acelerar e desacelerar sozinho, e — o que mais importa — um
#: trecho sem telemetria é comprimido para fora de vista em vez de aparecer
#: como vazio. Ausência de dado é dado.
#:
#: Trinta segundos é o horizonte que o piloto relaciona com o que acabou de
#: fazer. O eixo por distância saiu do painel: ao vivo a pergunta é sempre
#: "o que acabou de acontecer", e distância não responde isso.
TRAIL_WINDOW_S = 30.0

REPAINT_INTERVAL_MS = 66  # ~15 Hz

#: Largura mínima do botão de conexão, em px. Cabe "conectando…", que é o
#: mais longo dos cinco rótulos que ele assume.
CONNECT_BUTTON_MIN_W = 130


def _parece_endereco_ip(texto: str) -> bool:
    """Quatro grupos de dígitos separados por ponto — nome de pista nenhum é."""
    partes = texto.split(".")
    return len(partes) == 4 and all(p.strip().isdigit() for p in partes)


class LivePage(Page):
    page_id = "live"
    nav_title = "Ao vivo"
    title = "Ao vivo"
    subtitle = "Telemetria em tempo real"

    def __init__(
        self, core: CoreApplication, theme: Theme, view_model: LiveViewModel
    ) -> None:
        self._vm = view_model
        self._trail: list[tuple[float, float, float, float, float]] = []
        self._pending_repaint = False
        super().__init__(core, theme)
        self._connect()

    # ---------- construção ----------

    def build(self) -> None:
        self.header.add_action(self._build_toolbar())

        # Selo de dados sintéticos. Nasceu de um relato real: *"o aplicativo tá
        # funcionando cheio de dados mocados"* — a pessoa abriu o programa, viu
        # velocidade, RPM e marcha se mexendo, e concluiu que estava conectada
        # ao console. Estava vendo o gerador.
        #
        # A fonte sintética é boa e precisa existir: permite conhecer o programa
        # inteiro sem PS5. O defeito não é ela — é ela ser **indistinguível** da
        # real. Um painel que mostra números inventados sem dizer que são
        # inventados não é uma demonstração, é uma armadilha; e o dano cresce
        # com a qualidade do gerador, porque quanto mais convincente, mais
        # tempo a pessoa perde antes de desconfiar.
        self._synthetic_badge = Badge("DADOS SINTÉTICOS — não é o seu PS5")
        self._synthetic_badge.set_color(self.theme.palette.yellow)
        self.content.addWidget(self._synthetic_badge)
        self._update_synthetic_badge()

        # Aviso de gravação bloqueada. O `SessionManager` já publicava
        # `LapDiscarded` com o motivo, e **ninguém mostrava** — o programa
        # sabia dizer "nenhuma pista definida" e guardava para si enquanto
        # o piloto rodava uma sessão inteira que não seria gravada.
        self._recording_badge = Badge("")
        self._recording_badge.set_color(self.theme.palette.red)
        self._recording_badge.setVisible(False)
        self.content.addWidget(self._recording_badge)

        self._grid = MetricGrid(columns=7)
        for key, label, unit in (
            ("speed", "Velocidade", "km/h"),
            ("gear", "Marcha", ""),
            ("rpm", "RPM", ""),
            ("delta", "Delta melhor volta", "s"),
            ("delta_prev", "Delta última volta", "s"),
            ("lap", "Volta", ""),
            ("distance", "Distância", "m"),
        ):
            self._grid.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._grid)

        traces = Card(f"Últimos {TRAIL_WINDOW_S:.0f} segundos")
        self._speed_chart = DistanceChart(
            self.theme,
            "Velocidade",
            unit="km/h",
            height=140,
            y_step=SPEED_STEP_KMH,
            y_top_min=SPEED_TOP_MIN_KMH,
        )
        self._pedals_chart = DistanceChart(
            self.theme, "Pedais", unit="%", height=120, y_range=(0.0, 100.0)
        )
        # Segundos, sempre. O painel perdeu o seletor de eixo — ao vivo a
        # pergunta é "o que acabou de acontecer", e distância não responde.
        for chart in (self._speed_chart, self._pedals_chart):
            chart.set_x_unit("s")
        self._apply_time_window()

        traces.add(self._speed_chart)
        traces.add(self._pedals_chart)
        self.content.addWidget(traces)

        pneus = Card("Temperatura dos pneus")
        self._tyres = TyreTemperatures(self.theme, height=150)
        pneus.add(self._tyres)
        self.content.addWidget(pneus)

        # O rádio fica logo abaixo dos traços e **acima** do esticador: numa
        # tela ao vivo o piloto olha de relance, e o que ele precisa ver não
        # pode estar colado no rodapé.
        self._radio = RadioCard(self.theme)
        self.content.addWidget(self._radio)
        self.content.addStretch(1)

        self._status = QLabel("Parado")
        self._status.setObjectName(OBJ_STATUS_BAR)
        self.content.addWidget(self._status)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._track_input = QComboBox()
        self._track_input.setEditable(True)
        self._track_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        editor = self._track_input.lineEdit()
        if editor is not None:
            editor.setPlaceholderText("deixe vazio para detectar sozinho")
        self._configure_completer()
        self._reload_tracks()
        # **Vazio por padrão.** Um combo carregado com 105 pistas seleciona a
        # primeira em ordem alfabética sozinho, e conectar sem tocar no campo
        # gravava a sessão como "24 Heures du Mans" — um nome que ninguém
        # digitou e que ninguém desconfia, porque parece escolhido. Pior: com a
        # pista já definida, a detecção automática pelo comprimento nunca
        # rodava, já que ela só age quando não há pista. Era essa a causa de "a
        # detecção automática não está funcionando".
        self._track_input.setCurrentIndex(-1)
        self._track_input.setCurrentText("")
        # `activated` e não `currentTextChanged`: aquele dispara só por ação
        # de quem usa o programa, este dispararia também quando o código
        # repopula a lista — e aí recarregar as pistas aplicaria uma pista.
        self._track_input.activated.connect(self._on_track_chosen)
        editor_pista = self._track_input.lineEdit()
        if editor_pista is not None:
            editor_pista.editingFinished.connect(self._on_track_chosen)

        # O IP fica **à esquerda do botão**, colado nele: o botão diz o estado
        # da conexão pela cor, e o endereço diz com quem. Separados, "conectado"
        # não responde "a quê" — que é exatamente a pergunta de quem tem mais de
        # um console, ou trocou de rede e não lembra se o IP mudou.
        self._ps_ip_label = QLabel("")
        self._ps_ip_label.setObjectName(OBJ_SELECTOR_NOTE)
        self._refresh_ps_ip()

        self._start_button = QPushButton("Conectar")
        # O botão troca de texto conforme o estado, e o mais largo — "conectando…"
        # — é 60% maior que "Conectar". Sem um piso, o botão nasce do tamanho do
        # texto inicial e o Qt corta o resto: "CONECTADO" aparecia como
        # "ONECTAD", que é pior que não ter rótulo nenhum.
        self._start_button.setMinimumWidth(CONNECT_BUTTON_MIN_W)
        self._stop_button = QPushButton("Parar")
        self._stop_button.setObjectName(OBJ_GHOST_BUTTON)
        self._stop_button.setEnabled(False)

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_input)
        layout.addWidget(self._ps_ip_label)
        layout.addWidget(self._start_button)
        layout.addWidget(self._stop_button)
        return bar

    def _refresh_ps_ip(self) -> None:
        """Lê o IP da configuração a cada chamada, nunca de um valor guardado.

        A fonte e o IP podem mudar com o programa aberto, pela página de
        Configurações. Um rótulo decidido só na construção continuaria exibindo
        o endereço antigo depois de o novo já estar em uso — e um IP errado na
        tela é pior que nenhum, porque manda procurar defeito no console certo.
        """
        telemetry = self.core.settings.telemetry
        if telemetry.source.strip().lower() != "udp":
            self._ps_ip_label.setText("")
            return
        ip = telemetry.ps_ip.strip()
        self._ps_ip_label.setText(f"PS5: {ip}" if ip else "PS5: sem IP configurado")


    def _connect_radio(self) -> None:
        """Liga o detector do núcleo ao engenheiro, passando pela thread da UI.

        O núcleo publica `RaceEventDetected` na thread de captura; o adaptador
        entrega aqui. Só então se pede a nota — porque pedir de lá tocaria o
        serviço Qt de fora da thread dele.
        """
        self._voice = _build_voice(self.core.settings)

        service = self.core.engineer_service
        if service is None or not service.is_available:
            self._radio.show_unavailable()
            return

        self._vm.adapter.subscribe(RaceEventDetected, self._on_race_event)
        service.started.connect(self._on_engineer_started)
        service.ready.connect(self._on_advice)
        service.finished.connect(self._on_engineer_finished)

    def _on_race_event(self, event: RaceEventDetected) -> None:
        """Um evento virou pedido de nota — se o orçamento deixar.

        A cadência não é decidida aqui: `Budget` já impõe silêncio mínimo e cota
        por volta, e é ele que impede o rádio de falar oito vezes numa volta
        ruim. Esta função só traduz o evento em contexto.
        """
        service = self.core.engineer_service
        if service is None:
            return

        race = event.event
        session = self.core.session_manager.session
        service.request_quick_note(
            _situation(
                track=session.track.name if session.track else "",
                lap_number=session.lap_count + 1,
                delta_best_s=self._vm.delta_best,
                distance_m=race.distance_m,
                event=race.describe(),
            ),
            fallback=race.describe(),
        )

    def _on_engineer_started(self, level: str) -> None:
        if level == "quick":
            self._radio.show_thinking()

    def _on_advice(self, advice: object) -> None:
        if str(getattr(getattr(advice, "level", ""), "value", "")) != "quick":
            return
        self._radio.show_advice(advice)
        if self._voice is not None:
            # A voz recebe o **mesmo** conselho que o cartão. Se um dia os dois
            # divergirem, o piloto ouve uma coisa e lê outra — e passa a não
            # confiar em nenhum dos dois.
            self._voice.announce(advice)

    def _on_engineer_finished(self, level: str) -> None:
        """Sem nota, o rádio volta ao silêncio em vez de ficar pensando.

        "Não tenho nada a dizer" é resposta válida do nível 1 — e antes deste
        sinal ela deixava o cartão em "…" indefinidamente, que na tela é
        indistinguível de um programa travado.
        """
        if level == "quick" and not self._radio.has_note:
            self._radio.show_idle()

    def _connect(self) -> None:
        self._connect_radio()
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)

        self._vm.frame_updated.connect(self._on_frame)
        self._vm.delta_updated.connect(self._on_delta)
        self._vm.connection_changed.connect(self._on_connection)
        self._vm.stale_entered.connect(self._on_stale)
        self._vm.lap_saved.connect(self._on_lap_saved)

        # Repintura desacoplada da chegada de quadros: acumula e desenha a 15 Hz.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._repaint_traces)
        self._repaint_timer.start()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ---------- ações ----------

    def _update_synthetic_badge(self) -> None:
        """Mostra o selo enquanto a fonte for o gerador.

        Lê a configuração a cada chamada em vez de guardar o estado: a fonte
        agora pode ser trocada com o programa aberto, pela página de
        Configurações, e um selo que só se decide na construção continuaria
        dizendo "sintético" depois de o PS5 já estar conectado — que é a mesma
        mentira, na direção oposta.
        """
        self._synthetic_badge.setVisible(
            self.core.settings.telemetry.source.strip().lower() == "mock"
        )

    def try_autoconnect(self) -> bool:
        """Tenta conectar sozinho ao que já está configurado.

        O IP do PS5 já fica salvo; esperar um clique em "Conectar" toda vez é
        pedir de novo uma informação que o programa já tem. Só age quando a
        fonte é a rede e o IP está preenchido — com a fonte sintética, iniciar
        sozinho encheria a tela de dados inventados antes de alguém pedir, que é
        exatamente o mal-entendido que o selo de dados sintéticos existe para
        desfazer.

        Devolve se chegou a iniciar, para quem chama poder dizer na barra de
        status o que aconteceu.
        """
        telemetry = self.core.settings.telemetry
        if not self.should_autoconnect():
            return False

        self._on_start()
        self._status.setText(f"Conectando sozinho a {telemetry.ps_ip}…")
        return True

    def should_autoconnect(self) -> bool:
        """A decisão, separada da ação.

        Existe apartada porque a ação abre um socket, e um teste que precise
        abrir socket para verificar uma regra de negócio testa a rede, não a
        regra.
        """
        telemetry = self.core.settings.telemetry
        return telemetry.source == "udp" and bool(telemetry.ps_ip.strip())

    def on_enter(self) -> None:
        # Não é `refresh()`: `on_enter` roda toda vez que a página aparece,
        # enquanto `refresh()` depende do estado sujo. Vindo de Configurações,
        # a fonte pode ter mudado sem nada mais ter mudado junto.
        self._update_synthetic_badge()
        self._refresh_ps_ip()
        # A lista de pistas também: ela era montada uma vez, na construção da
        # página, e nunca mais. Renomear uma pista no Histórico não aparecia
        # aqui até reiniciar o programa — o campo continuava oferecendo o nome
        # velho, que já não existia no banco.
        self._reload_tracks()
        super().on_enter()

    def _configure_completer(self) -> None:
        """Busca por trecho e sem diferenciar maiúsculas, nas 105 pistas.

        O completador padrão do Qt casa **prefixo** e respeita caixa: com o
        catálogo carregado, digitar "interlagos" não achava "Interlagos", e
        "lagos" não achava nada, porque o nome no catálogo é "Autódromo José
        Carlos Pace". Numa lista de 105 itens isso equivale a não ter busca —
        sobra rolar até achar, que é o que fazia o campo parecer quebrado.
        """
        completer = self._track_input.completer()
        if completer is None:
            return
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )

    def _reload_tracks(self) -> None:
        """As pistas já usadas primeiro, depois o catálogo do jogo.

        Nesta ordem porque quem volta ao programa costuma voltar ao mesmo
        circuito. O catálogo entra embaixo para que digitar continue funcionando
        e, principalmente, para que o nome venha escrito igual toda vez — sem
        ele, "Suzuka" e "suzuka circuit" viram duas pistas distintas no banco e
        o histórico se parte em dois sem ninguém perceber.
        """
        current = self._track_input.currentText() if self._track_input.count() else ""
        self._track_input.clear()

        seen: set[str] = set()
        for track in self.core.tracks.get_all():
            self._track_input.addItem(track.name, track.id)
            seen.add(track.name.lower())

        catalog_names = sorted(
            (t.name for t in self.core.catalog.tracks.values() if t.name),
            key=str.lower,
        )
        for name in catalog_names:
            if name.lower() not in seen:
                self._track_input.addItem(name, None)

        if current:
            self._track_input.setCurrentText(current)
        else:
            # **Campo vazio continua vazio.** `clear()` seguido de `addItem()`
            # deixa o `currentIndex` em 0, então repopular a lista escrevia
            # sozinho a primeira pista em ordem alfabética num campo que a
            # pessoa tinha deixado em branco de propósito. Era o defeito de
            # "gravou a sessão como 24 Heures du Mans" voltando por outra porta:
            # antes ele nascia na construção da página, agora nasceria a cada
            # vez que a aba fosse aberta. Com a pista preenchida sozinha, a
            # detecção automática também nunca roda — ela só age sem pista.
            self._track_input.setCurrentIndex(-1)
            self._track_input.setCurrentText("")

    def _resolve_track_name(self) -> str:
        """Lê o texto digitado, não `currentData()`.

        Num QComboBox editável com NoInsert, `setCurrentText()` não move o
        `currentIndex` — `currentData()` devolveria sempre o item 0. Esse foi um
        bug real: com o catálogo carregado, qualquer pista digitada era gravada
        como a primeira em ordem alfabética.
        """
        nome = self._track_input.currentText().strip()
        # O campo de conexão mora em Configurações; aqui é o nome do circuito.
        # A confusão aconteceu de verdade — uma sessão inteira foi gravada sob
        # "192.168.15.156" — e o histórico passa a agrupar voltas por um rótulo
        # que não é pista nenhuma.
        if _parece_endereco_ip(nome):
            self._status.setText(
                f"'{nome}' parece um endereço de rede, não uma pista. "
                "O IP do PS5 se configura em Configurações."
            )
            return ""
        return nome

    def _apply_track_from_field(self) -> bool:
        """Leva o que está escrito no campo para a sessão. Devolve se aplicou.

        Existe como método próprio, e ligado ao sinal do combo, porque a
        pista **só** era lida no instante do clique em Conectar. Com o
        autoconectar disparando na abertura — quando o campo nasce vazio de
        propósito — ninguém mais chamava `set_track`, e sem pista o
        `SessionManager` bloqueia a gravação: toda volta virava
        `LapDiscarded("nenhuma pista definida")`, em silêncio.

        Escolher a pista no meio da sessão passa a valer, que é o que
        qualquer um espera de um campo que oferece uma lista.
        """
        nome = self._resolve_track_name()
        if not nome:
            return False

        track_id = self.core.tracks.get_or_create(nome)
        mudou = self.core.session_manager.set_track(Track(id=track_id, name=nome))
        if mudou:
            self._reload_tracks()
            self._track_input.setCurrentText(nome)
        self._refresh_recording_hint()
        return True

    def _refresh_recording_hint(self) -> None:
        """Mostra, na tela, se a volta que fechar agora será gravada.

        A regra vive no `SessionManager` e o motivo já vinha pronto em
        `blocked_reason`; o que faltava era alguém exibi-lo. Sem isto, uma
        sessão inteira pode ser rodada e descartada sem um sinal — foi
        exatamente o que aconteceu.

        Só aparece com a captura de pé: parado, "não vai gravar" é óbvio e o
        aviso viraria mobília que se aprende a ignorar.
        """
        motivo = self.core.session_manager.blocked_reason
        mostrar = bool(motivo) and self.core.source.is_running
        if mostrar:
            self._recording_badge.setText(
                f"VOLTAS NÃO ESTÃO SENDO GRAVADAS — {motivo}. "
                "Escolha a pista no campo acima."
            )
        self._recording_badge.setVisible(mostrar)

    def _on_track_chosen(self) -> None:
        """O campo de pista mudou por ação de quem usa o programa."""
        self._apply_track_from_field()

    def _on_start(self) -> None:
        self._apply_track_from_field()

        self._trail.clear()
        self.core.start()
        # A fonte pode ter mudado desde que esta página foi montada — é o que
        # acontece sempre que alguém salva Configurações e volta. Um selo
        # decidido só na entrada da página continuava gritando "SINTÉTICOS"
        # com o PS5 já conectado, e essa mentira é pior que a ausência do selo:
        # ela faz o piloto duvidar de dados que estão corretos.
        self._update_synthetic_badge()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._refresh_recording_hint()

    def _on_track_candidates(self, names: list[str]) -> None:
        """Mostra o que o comprimento da volta sugere.

        Um candidato: já foi aplicado pelo núcleo, e o campo só reflete. Vários:
        eles entram no combo com o mais provável na frente, e a barra pede a
        confirmação — porque escolher sozinho entre circuitos de comprimento
        parecido é errar em silêncio.
        """
        if not names or self._track_input.currentText().strip():
            return

        if len(names) == 1:
            self._track_input.setCurrentText(names[0])
            self._status.setText(f"Pista identificada pelo comprimento: {names[0]}")
            return

        for posicao, nome in enumerate(names):
            existente = self._track_input.findText(nome)
            if existente >= 0:
                self._track_input.removeItem(existente)
            self._track_input.insertItem(posicao, nome)
        self._status.setText(
            f"Pista provável: {names[0]} — confirme no campo (outros: "
            f"{', '.join(names[1:3])})"
        )

    def _on_stop(self) -> None:
        """Desconecta mostrando que está desconectando.

        `core.stop()` **bloqueia** — a thread de captura pode estar parada em
        `recvfrom` e só percebe a parada quando o socket expira, o que leva até
        3,5 s. Sem aviso, a janela congela com o botão ainda verde escrito
        CONECTADO: quem clicou conclui que o clique não pegou e clica de novo.
        Pintar antes é o que transforma travamento aparente em espera com
        explicação.

        `processEvents` é o que faz a pintura acontecer **agora**: sem ele o Qt
        só redesenharia ao voltar ao laço de eventos, que é depois do `stop()`
        — exatamente tarde demais para servir de aviso.
        """
        self._paint_connection(ConnectionState.DISCONNECTING.value)
        self._stop_button.setEnabled(False)
        self._status.setText("Desconectando…")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

        self._update_synthetic_badge()
        if self._voice is not None:
            self._voice.silence()
        self.core.stop()

        self._start_button.setEnabled(True)
        self._paint_connection(ConnectionState.DISCONNECTED.value)
        self._status.setText("Parado")
        self._refresh_recording_hint()

    # ---------- reação ----------

    def _on_frame(self, event: TelemetryReceived) -> None:
        point = event.point
        cards = self._grid.cards
        cards["speed"].set_value(f"{point.speed_kmh:.0f}")
        cards["gear"].set_value(str(point.gear) if point.gear > 0 else "N")
        cards["rpm"].set_value(f"{point.rpm:.0f}")
        cards["distance"].set_value(f"{point.distance_m:.0f}")
        cards["lap"].set_value(str(event.frame.lap_count))
        self._tyres.set_temperatures(
            event.frame.tire_temp_fl,
            event.frame.tire_temp_fr,
            event.frame.tire_temp_rl,
            event.frame.tire_temp_rr,
        )

        self._trail.append(
            (
                point.distance_m,
                point.elapsed_ms / 1000.0,
                point.speed_kmh,
                point.throttle,
                point.brake,
            )
        )
        # Descarta o que saiu da janela de rastro, mantendo a lista curta em vez
        # de crescer a volta inteira.
        # Volta nova: o tempo decorrido **zera**, e a distância também. Um
        # rastro que atravessa a virada tem tempos indo de 102 s a 0 s, e o eixo
        # de tempo passa a medir 103 s de janela — foi o que apareceu ao medir.
        # Manter só a volta corrente é o que faz a janela de 30 s significar
        # trinta segundos.
        if len(self._trail) > 1 and self._trail[-1][1] < self._trail[-2][1]:
            self._trail = self._trail[-1:]

        # Corte só por **tempo**: o painel mede segundos, e cortar também por
        # distância descartaria amostras dentro da janela sempre que o carro
        # estivesse rápido — janela de 30 s que às vezes guarda 12 não é
        # janela de 30 s.
        corte_s = point.elapsed_ms / 1000.0 - TRAIL_WINDOW_S
        if self._trail[0][1] < corte_s:
            self._trail = [row for row in self._trail if row[1] >= corte_s]
        self._pending_repaint = True

    def _apply_time_window(self) -> None:
        """Ancora os dois gráficos nos últimos N segundos, sempre.

        A janela termina no instante mais recente que chegou e recua N
        segundos — mesmo que não haja dado para preencher. Sem isso o eixo
        seguia os dados: nos primeiros instantes de captura ele media 1 s,
        depois 2, depois 3, e o traço percorria a largura toda enquanto
        existia um piscar de dado. A escala mudava embaixo do olho a cada
        repintura, e um trecho sem telemetria — o que mais importa notar —
        era comprimido para fora de vista em vez de aparecer como vazio.
        """
        fim = self._trail[-1][1] if self._trail else 0.0
        janela = (fim - TRAIL_WINDOW_S, fim)
        for chart in (self._speed_chart, self._pedals_chart):
            chart.set_x_window(janela)

    def _repaint_traces(self) -> None:
        if not self._pending_repaint:
            return
        self._pending_repaint = False

        palette = self.theme.palette
        self._apply_time_window()
        self._speed_chart.set_series(
            [
                Series(
                    "vel",
                    palette.channel_speed,
                    [(row[1], row[2]) for row in self._trail],
                )
            ]
        )
        self._pedals_chart.set_series(
            [
                Series(
                    "acel",
                    palette.channel_throttle,
                    [(row[1], row[3]) for row in self._trail],
                ),
                Series(
                    "freio",
                    palette.channel_brake,
                    [(row[1], row[4]) for row in self._trail],
                ),
            ]
        )

    def _on_delta(self, best: float | None, previous: float | None) -> None:
        """Os dois deltas, rotulados.

        Antes havia um cartão só, escrito "Delta", mostrando a diferença contra
        a **melhor** volta — e o delta contra a anterior já era calculado e
        descartado. Sem rótulo, não havia como saber qual dos dois se estava
        lendo, e são perguntas diferentes: a melhor mede quanto falta para o seu
        teto, a anterior mede se você está evoluindo agora.
        """
        for chave, valor in (("delta", best), ("delta_prev", previous)):
            card = self._grid.cards[chave]
            if valor is None:
                card.set_value("—", self.theme.palette.text_muted)
            else:
                card.set_value(f"{valor:+.3f}", self.theme.palette.delta(valor))

    def _on_connection(self, state: str, message: str) -> None:
        self._status.setText(message or f"Conexão: {state}")
        self._paint_connection(state)

    def _paint_connection(self, state: str) -> None:
        """Pinta o botão pelo estado da conexão.

        **Verde só em `RECEIVING`**, isto é, só com pacote do console na mão. O
        UDP não tem aperto de mão: abrir o socket e mandar o heartbeat sempre
        "funciona", mesmo com o PS5 desligado ou com o IP errado. Pintar de
        verde ao clicar em Conectar seria afirmar uma conexão que ninguém
        verificou — o mesmo tipo de mentira que o selo de dados sintéticos
        existe para evitar.

        Amarelo é "tentando", vermelho é "estava recebendo e parou", que é o
        estado que interessa notar no meio de uma sessão.
        """
        palette = self.theme.palette
        cores = {
            ConnectionState.RECEIVING.value: (palette.green, "CONECTADO"),
            ConnectionState.CONNECTING.value: (palette.yellow, "conectando…"),
            ConnectionState.DISCONNECTING.value: (
                palette.text_muted,
                "desconectando…",
            ),
            ConnectionState.NO_SIGNAL.value: (palette.red, "sem sinal"),
            ConnectionState.ERROR.value: (palette.red, "erro"),
        }
        cor, rotulo = cores.get(state, ("", "Conectar"))

        self._start_button.setText(rotulo)
        self._start_button.setStyleSheet(
            f"background-color: {cor}; color: {palette.accent_text};" if cor else ""
        )

    def _on_stale(self) -> None:
        """Sem pacote há um tempo: **congela** os números em vez de apagá-los.

        Apagar era a escolha anterior, para distinguir "carro parado" de
        "transmissão perdida". Mas o caso comum é o jogo pausado, e ali apagar
        destrói justamente o que se quer olhar: os valores do instante em que se
        pausou. Congelar preserva, e a barra de status assume a tarefa de dizer
        que aquilo não é mais tempo real — que era o único motivo de apagar.
        """
        self._status.setText("PAUSADO ou sem telemetria — valores congelados")

    def _on_lap_saved(self, event: LapSaved) -> None:
        minutes, remainder = divmod(event.lap.lap_time_ms, 60_000)
        seconds, millis = divmod(remainder, 1000)
        marker = " ★ melhor" if event.is_best else ""
        self._status.setText(
            f"Volta gravada: {minutes}:{seconds:02d}.{millis:03d}{marker}"
        )
        self._trail.clear()

    def _refresh_stats(self) -> None:
        if not self.core.source.is_running:
            return
        stats = self.core.metrics.snapshot()
        if stats.packets_received:
            self._status.setText(stats.format_summary())

    def close_page(self) -> None:
        self._repaint_timer.stop()
        self._stats_timer.stop()


def _situation(
    *,
    track: str,
    lap_number: int,
    delta_best_s: float | None,
    distance_m: float,
    event: str,
) -> str:
    """Contexto da nota de rádio, sem importar `gt7ai` no topo do módulo.

    O import fica dentro da função porque esta página tem de montar com o plugin
    ausente — e a chamada só acontece quando o serviço se declarou disponível.
    Se mesmo assim o pacote sumir, o contexto degrada para o texto do evento em
    vez de derrubar a tela ao vivo.
    """
    try:
        from gt7ai.prompts import format_live_situation
    except ImportError:  # pragma: no cover - só sem o plugin
        return event

    return format_live_situation(
        track=track,
        lap_number=lap_number,
        delta_ms=None if delta_best_s is None else delta_best_s * 1000.0,
        where=f"{distance_m:.0f} m da linha",
        event=event,
    )


def _build_voice(settings: object) -> VoiceRadio | None:
    """Monta o rádio falado, ou `None` se a voz estiver desligada ou ausente.

    Import local pelo mesmo motivo do `gt7ai`: a interface tem de montar com o
    plugin de voz ausente, e uma máquina sem sintetizador deve rodar o programa
    em silêncio em vez de deixar de abrir.
    """
    config = getattr(settings, "voice", None)
    if config is None or not getattr(config, "enabled", False):
        return None
    try:
        from gt7voice import VoiceRadio, build_speaker
    except ImportError:
        return None
    return VoiceRadio(build_speaker(config), config)
