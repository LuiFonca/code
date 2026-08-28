"""
A página que faltava — e cuja ausência fazia o programa parecer quebrado.

Até aqui, toda a configuração vivia no `.env`, documentada no README. Isso é
suficiente para quem monta o projeto e falso para todo mundo. O relato que
originou esta página: *"o aplicativo tá funcionando cheio de dados mocados, não
funciona a conexão com o PS5"*. Não havia conexão falhando — a fonte sintética
é o padrão, e não existia caminho pela interface para trocá-la. A pessoa fez
tudo certo e viu dados inventados.

Três coisas que este arquivo trata como obrigação, não enfeite:

**Salvar tem que persistir.** Um formulário que aplica no processo e esquece ao
fechar é pior que nenhum, porque a pessoa acredita que configurou. Grava no
`.env` pelo `gt7core.config.persistence`, que edita as linhas da chave e não
encosta nos comentários.

**Salvar tem que surtir efeito agora.** Trocar para o PS5 remonta a fonte no
processo vivo (`core.reconfigure_source`). Acertar rede já é chato o bastante
sem um reinício por tentativa.

**Quando não surtir efeito, tem que dizer.** A precedência documentada é
ambiente > `.env` > padrão, e está certa. Mas ela cria o pior modo de falha de
uma tela de configuração: grava certo, e nada muda, porque um `export`
esquecido continua vencendo. Aqui isso vira um aviso na tela, não um mistério.

O teste de conexão roda **fora da thread da interface** pelo mesmo motivo do
`EngineerService`: são segundos de socket, e um botão que congela a janela
parece um programa travado — a pessoa clica de novo, ou fecha.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gt7core.config.persistence import overridden_by_environment, save_env
from gt7core.tools.diagnose import Diagnosis, probe

from ..design.tokens import Space, Theme
from ..widgets.cards import Card
from .base import Page

#: Rótulo visível → valor gravado. A pessoa escolhe "PS5 na rede", não "udp".
SOURCE_LABELS: dict[str, str] = {
    "Telemetria sintética (sem PS5)": "mock",
    "PS5 na rede": "udp",
    "Sessão gravada (replay)": "replay",
}
SOURCE_VALUES = {value: label for label, value in SOURCE_LABELS.items()}

#: Segundos de sondagem no teste da interface. Menor que os 20 s da linha de
#: comando: aqui há um botão desabilitado e um "testando..." na tela, e a
#: paciência de quem olha para uma janela é menor que a de quem olha um terminal.
PROBE_SECONDS = 6.0


class _ProbeSignals(QObject):
    done = Signal(object)


class _ProbeTask(QRunnable):
    """A sondagem de rede como tarefa isolada — recebe IP, devolve veredito."""

    def __init__(self, ip: str, signals: _ProbeSignals) -> None:
        super().__init__()
        self._ip = ip
        self._signals = signals

    def run(self) -> None:
        try:
            result = probe(self._ip, wait_s=PROBE_SECONDS)
        except Exception as exc:  # pragma: no cover - depende de rede
            result = Diagnosis(
                ok=False,
                headline="O teste falhou antes de chegar à rede.",
                steps=(str(exc),),
            )
        self._signals.done.emit(result)


class SettingsPage(Page):
    """Telemetria, Discord, IA e voz — tudo que hoje só existe no `.env`."""

    page_id = "settings"
    nav_title = "Configurações"
    title = "Configurações"
    subtitle = "Fonte de telemetria, Discord, IA e voz"

    def __init__(self, core: Any, theme: Theme) -> None:
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._probe_signals = _ProbeSignals()
        super().__init__(core, theme)
        self._probe_signals.done.connect(self._on_probe_done)
        self._load_from_settings()

    # ---------- montagem ----------

    def build(self) -> None:
        """Quatro cartões roláveis, e o rodapé de salvar sempre à vista.

        A rolagem não é refinamento: com os quatro cartões empilhados, a página
        passa de mil pixels de altura, e num laptop — a máquina alvo é um
        MacBook — o botão "Salvar e aplicar" simplesmente **não existiria** na
        tela. Descobri isto renderizando a página, não lendo o código: o
        primeiro desenho cortava o cartão de Voz no meio.

        Por isso o rodapé fica **fora** da área rolável. Salvar é a ação que a
        página inteira serve, e caçá-la rolando até o fim é o tipo de atrito que
        faz alguém desistir e voltar a editar o `.env` na mão.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, Space.MD.px, 0)
        column.setSpacing(Space.LG.px)

        column.addWidget(self._build_telemetry_card())
        column.addWidget(self._build_vehicle_card())
        column.addWidget(self._build_discord_card())
        column.addWidget(self._build_ai_card())
        column.addWidget(self._build_voice_card())
        column.addStretch(1)

        scroll.setWidget(inner)
        self.content.addWidget(scroll, 1)

        self._env_warning = QLabel()
        self._env_warning.setWordWrap(True)
        self._env_warning.setVisible(False)
        self.content.addWidget(self._env_warning)

        self._save_button = QPushButton("Salvar e aplicar")
        self._save_button.clicked.connect(self._on_save)
        self._save_status = QLabel()
        self._save_status.setWordWrap(True)

        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._save_button)
        row.addWidget(self._save_status, 1)
        self.content.addWidget(footer)

    def _build_telemetry_card(self) -> Card:
        card = Card("Telemetria")
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._source = QComboBox()
        self._source.addItems(list(SOURCE_LABELS))
        self._source.currentTextChanged.connect(self._on_source_changed)

        self._ps_ip = QLineEdit()
        self._ps_ip.setPlaceholderText("192.168.1.50 — PS5: Ajustes > Rede > Status")

        self._send_port = QSpinBox()
        self._send_port.setRange(1, 65535)
        self._receive_port = QSpinBox()
        self._receive_port.setRange(1, 65535)

        self._mock_speed = QSpinBox()
        self._mock_speed.setRange(1, 200)
        self._mock_speed.setSuffix("×")

        layout.addRow("Fonte:", self._source)
        layout.addRow("IP do PlayStation:", self._ps_ip)
        layout.addRow("Porta de envio:", self._send_port)
        layout.addRow("Porta de recepção:", self._receive_port)
        layout.addRow("Velocidade da simulação:", self._mock_speed)
        card.add(form)

        self._test_button = QPushButton("Testar conexão")
        self._test_button.clicked.connect(self._on_test)
        card.add(self._test_button)

        self._test_result = QLabel()
        self._test_result.setWordWrap(True)
        self._test_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card.add(self._test_result)
        return card

    def _build_vehicle_card(self) -> Card:
        """As duas grandezas que o GT7 não transmite, editáveis por quem sabe.

        O gráfico de volante da Análise é a curvatura da trajetória convertida
        por estes dois números. Eles não vêm do pacote nem do catálogo do jogo,
        e por isso estão aqui em vez de fixos no código: um valor que o programa
        supôs sozinho é um palpite; um valor que você declarou é um dado seu.

        `QDoubleSpinBox`, e não campo de texto, porque a faixa aceitável é
        conhecida e estreita — um entre-eixos de 26 m viria de um dedo errado no
        teclado, e o gráfico sairia dez vezes maior sem nada explicar por quê.
        """
        card = Card("Carro — para o gráfico de volante")
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._wheelbase = QDoubleSpinBox()
        self._wheelbase.setRange(1.5, 4.0)
        self._wheelbase.setSingleStep(0.05)
        self._wheelbase.setDecimals(2)
        self._wheelbase.setSuffix(" m")

        self._steering_ratio = QDoubleSpinBox()
        self._steering_ratio.setRange(5.0, 30.0)
        self._steering_ratio.setSingleStep(0.5)
        self._steering_ratio.setDecimals(1)
        self._steering_ratio.setSuffix(" :1")

        layout.addRow("Entre-eixos:", self._wheelbase)
        layout.addRow("Relação de direção:", self._steering_ratio)
        card.add(form)

        nota = QLabel(
            "Referências: Miata ~2,31 m e 15:1; GT3 de corrida ~2,55 m e 12:1; "
            "protótipo de Le Mans ~2,90 m e 11:1. Os dois só escalam o eixo do "
            "gráfico de volante — a forma do traço não muda."
        )
        nota.setWordWrap(True)
        card.add(nota)
        return card

    def _build_discord_card(self) -> Card:
        card = Card("Discord")
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._discord_enabled = QCheckBox("Enviar debrief para o Discord")

        self._discord_token = QLineEdit()
        self._discord_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token.setPlaceholderText("token do bot — fica só no seu .env")

        # Nome, não ID. Ninguém sabe de cor o ID numérico de um servidor, e
        # copiá-lo exige ligar o modo desenvolvedor do Discord. O nome é o que
        # a pessoa vê na barra lateral.
        self._discord_guild = QLineEdit()
        self._discord_guild.setPlaceholderText("nome do servidor (vazio = o primeiro)")
        self._discord_channel = QLineEdit()
        self._discord_channel.setPlaceholderText("nome do canal, sem #  (ex.: telemetria)")

        layout.addRow("", self._discord_enabled)
        layout.addRow("Token:", self._discord_token)
        layout.addRow("Servidor:", self._discord_guild)
        layout.addRow("Canal:", self._discord_channel)
        card.add(form)

        hint = QLabel(
            "Habilite MESSAGE CONTENT INTENT no portal do Discord, ou os comandos "
            "não chegam. Mudanças aqui valem na próxima vez que o programa abrir."
        )
        hint.setWordWrap(True)
        card.add(hint)
        return card

    def _build_ai_card(self) -> Card:
        card = Card("Race Engineer")
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._ai_enabled = QCheckBox("Usar modelo de linguagem")
        self._ai_url = QLineEdit()
        self._ai_url.setPlaceholderText("http://localhost:11434/v1")
        self._ai_model = QLineEdit()
        self._ai_model.setPlaceholderText("qwen3:4b")
        self._ai_timeout = QSpinBox()
        self._ai_timeout.setRange(5, 300)
        self._ai_timeout.setSuffix(" s")

        layout.addRow("", self._ai_enabled)
        layout.addRow("Servidor local:", self._ai_url)
        layout.addRow("Modelo:", self._ai_model)
        layout.addRow("Tempo limite:", self._ai_timeout)
        card.add(form)

        hint = QLabel(
            "Desligado, ou se o modelo demorar demais, o conselho sai da análise "
            "numérica — que é exata e não custa memória."
        )
        hint.setWordWrap(True)
        card.add(hint)
        return card

    def _build_voice_card(self) -> Card:
        card = Card("Voz")
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._voice_enabled = QCheckBox("Falar a nota de rádio em voz alta")
        self._voice_name = QLineEdit()
        self._voice_name.setPlaceholderText("macOS: Luciana ou Felipe (`say -v ?`)")
        self._voice_rate = QSpinBox()
        self._voice_rate.setRange(80, 400)
        self._voice_rate.setSuffix(" ppm")

        layout.addRow("", self._voice_enabled)
        layout.addRow("Voz do sistema:", self._voice_name)
        layout.addRow("Ritmo:", self._voice_rate)
        card.add(form)
        return card

    # ---------- estado ----------

    def _load_from_settings(self) -> None:
        """Preenche os campos com o que está valendo agora."""
        settings = self.core.settings
        telemetry = settings.telemetry

        self._source.setCurrentText(
            SOURCE_VALUES.get(telemetry.source, next(iter(SOURCE_LABELS)))
        )
        self._ps_ip.setText(telemetry.ps_ip)
        self._send_port.setValue(telemetry.send_port)
        self._receive_port.setValue(telemetry.receive_port)
        self._mock_speed.setValue(int(telemetry.mock_speed_multiplier))

        self._wheelbase.setValue(settings.vehicle.wheelbase_m)
        self._steering_ratio.setValue(settings.vehicle.steering_ratio)

        self._discord_enabled.setChecked(settings.discord.enabled)
        self._discord_guild.setText(getattr(settings.discord, "guild", ""))
        self._discord_channel.setText(getattr(settings.discord, "channel", ""))
        # O token nunca volta para a tela: `SecretStr` existe para isso. Um
        # campo preenchido com o valor real seria copiável, e o segredo passaria
        # a depender de ninguém apertar Ctrl+C.
        self._discord_token.setPlaceholderText(
            "token já configurado — deixe em branco para manter"
            if settings.discord.token.reveal()
            else "token do bot — fica só no seu .env"
        )

        self._ai_enabled.setChecked(settings.ai.enabled)
        self._ai_url.setText(settings.ai.local_url)
        self._ai_model.setText(settings.ai.local_model)
        self._ai_timeout.setValue(int(settings.ai.local_timeout_s))

        self._voice_enabled.setChecked(settings.voice.enabled)
        self._voice_name.setText(settings.voice.voice)
        self._voice_rate.setValue(settings.voice.rate_wpm)

        self._on_source_changed(self._source.currentText())
        self._show_environment_warning()

    def _on_source_changed(self, label: str) -> None:
        """Mostra só o que importa para a fonte escolhida.

        Campo irrelevante ligado não é neutro: um IP visível na fonte sintética
        sugere que preenchê-lo faria alguma diferença.
        """
        kind = SOURCE_LABELS.get(label, "mock")
        is_udp = kind == "udp"
        self._ps_ip.setEnabled(is_udp)
        self._send_port.setEnabled(is_udp)
        self._receive_port.setEnabled(is_udp)
        self._test_button.setEnabled(is_udp)
        self._mock_speed.setEnabled(kind == "mock")

    def _show_environment_warning(self) -> None:
        keys = overridden_by_environment(
            ("GT7_TELEMETRY_SOURCE", "GT7_PS_IP", "GT7_DISCORD_TOKEN")
        )
        if not keys:
            self._env_warning.setVisible(False)
            return
        self._env_warning.setText(
            "Atenção: " + ", ".join(keys) + " está definida no ambiente e vence o "
            "arquivo. O que você salvar aqui será gravado, mas não terá efeito "
            "enquanto essa variável existir."
        )
        self._env_warning.setStyleSheet(f"color: {self.theme.palette.yellow};")
        self._env_warning.setVisible(True)

    # ---------- ações ----------

    def _on_test(self) -> None:
        """Testa a conexão — ou explica por que testar agora mentiria.

        A sonda abre a **mesma porta 33740** que a captura. Com a captura
        rodando, os dois sockets ficam ligados na porta (o `SO_REUSEADDR`
        deixa) e o sistema entrega cada pacote a um deles. O resultado é o
        pior possível: a sonda recebe, anuncia FUNCIONANDO, e a aba Ao vivo
        fica vazia — porque os pacotes que ela precisava foram para a sonda.
        Pior ainda, o veredito verde manda procurar o defeito em qualquer
        lugar menos onde ele está.

        Com a captura de pé não há o que sondar: ela **é** a resposta, e os
        contadores dela dizem a verdade sem disputar nada.
        """
        ip = self._ps_ip.text().strip()
        if not ip:
            self._test_result.setText("Digite o IP do PlayStation primeiro.")
            return

        if self.core.source.is_running:
            self._report_running_capture()
            return

        self._test_button.setEnabled(False)
        self._test_result.setText(
            f"Testando {ip} por {PROBE_SECONDS:.0f}s — o GT7 precisa estar "
            "numa corrida ou track day, com o carro em pista."
        )
        self._pool.start(_ProbeTask(ip, self._probe_signals))

    def _report_running_capture(self) -> None:
        """O veredito vindo da captura que já está de pé."""
        stats = self.core.metrics.snapshot()
        palette = self.theme.palette

        if stats.packets_received == 0:
            self._test_result.setText(
                "A captura está ligada e não recebeu nenhum pacote.\n\n"
                "Não testo por fora agora porque a sonda usaria a mesma "
                "porta 33740 e roubaria os pacotes da captura — ela "
                "anunciaria sucesso enquanto a tela continua vazia.\n\n"
                "Confira: o GT7 está numa sessão com o carro em pista "
                "(menu e replay não transmitem)? O IP é o do console "
                "agora? Há outra ferramenta de telemetria aberta?"
            )
            self._test_result.setStyleSheet(f"color: {palette.red};")
            return

        idade = stats.last_packet_age_s
        recente = idade is not None and idade < 3.0
        self._test_result.setText(
            f"A captura já está recebendo: {stats.packets_received} "
            f"pacotes, {stats.packets_per_second:.0f}/s, "
            f"{stats.frames_emitted} quadros válidos"
            + (
                "."
                if recente
                else f" — mas o último chegou há {idade:.0f}s, então parou."
            )
            + (
                f"\n\n{stats.callback_errors} quadros foram perdidos por "
                "erro de quem os consome — a captura está boa e a tela "
                "não. Mande esta mensagem."
                if stats.callback_errors
                else ""
            )
        )
        saudavel = recente and not stats.callback_errors
        cor = palette.green if saudavel else palette.yellow
        self._test_result.setStyleSheet(f"color: {cor};")

    def _on_probe_done(self, diagnosis: Diagnosis) -> None:
        self._test_button.setEnabled(True)
        self._test_result.setText(diagnosis.summary())
        color = (
            self.theme.palette.green if diagnosis.ok else self.theme.palette.red
        )
        self._test_result.setStyleSheet(f"color: {color};")

    def _on_save(self) -> None:
        changes = self._collect()
        try:
            save_env(self.core.settings.env_path, changes)
        except OSError as exc:
            self._save_status.setText(f"Não foi possível gravar o .env: {exc}")
            self._save_status.setStyleSheet(f"color: {self.theme.palette.red};")
            return

        self._apply_to_running_core()
        self._show_environment_warning()

    def _collect(self) -> dict[str, str]:
        """Campos da tela → chaves do `.env`."""
        changes = {
            "GT7_TELEMETRY_SOURCE": SOURCE_LABELS[self._source.currentText()],
            "GT7_PS_IP": self._ps_ip.text().strip(),
            "GT7_SEND_PORT": str(self._send_port.value()),
            "GT7_RECEIVE_PORT": str(self._receive_port.value()),
            "GT7_MOCK_SPEED": str(self._mock_speed.value()),
            "GT7_WHEELBASE_M": f"{self._wheelbase.value():.2f}",
            "GT7_STEERING_RATIO": f"{self._steering_ratio.value():.1f}",
            "GT7_DISCORD_ENABLED": _boolean(self._discord_enabled.isChecked()),
            "GT7_DISCORD_GUILD": self._discord_guild.text().strip(),
            "GT7_DISCORD_CHANNEL": self._discord_channel.text().strip(),
            "GT7_AI_ENABLED": _boolean(self._ai_enabled.isChecked()),
            "GT7_AI_LOCAL_URL": self._ai_url.text().strip(),
            "GT7_AI_LOCAL_MODEL": self._ai_model.text().strip(),
            "GT7_AI_LOCAL_TIMEOUT_S": str(self._ai_timeout.value()),
            "GT7_VOICE_ENABLED": _boolean(self._voice_enabled.isChecked()),
            "GT7_VOICE_NAME": self._voice_name.text().strip(),
            "GT7_VOICE_RATE": str(self._voice_rate.value()),
        }
        # Token em branco significa "mantenha o que já está lá" — e não
        # "apague". Gravar vazio aqui desconectaria o bot de quem só quis
        # mudar o canal.
        token = self._discord_token.text().strip()
        if token:
            changes["GT7_DISCORD_TOKEN"] = token
        return changes

    def _apply_to_running_core(self) -> None:
        """Leva o que dá para levar ao processo vivo, e diz o que ficou para depois."""
        settings = self.core.settings
        telemetry = settings.telemetry
        previous_source = telemetry.source

        telemetry.source = SOURCE_LABELS[self._source.currentText()]
        telemetry.ps_ip = self._ps_ip.text().strip()
        telemetry.send_port = self._send_port.value()
        telemetry.receive_port = self._receive_port.value()
        telemetry.mock_speed_multiplier = float(self._mock_speed.value())

        settings.vehicle.wheelbase_m = self._wheelbase.value()
        settings.vehicle.steering_ratio = self._steering_ratio.value()

        settings.ai.enabled = self._ai_enabled.isChecked()
        settings.ai.local_url = self._ai_url.text().strip()
        settings.ai.local_model = self._ai_model.text().strip()
        settings.ai.local_timeout_s = float(self._ai_timeout.value())

        settings.voice.enabled = self._voice_enabled.isChecked()
        settings.voice.voice = self._voice_name.text().strip()
        settings.voice.rate_wpm = self._voice_rate.value()

        settings.discord.enabled = self._discord_enabled.isChecked()
        settings.discord.guild = self._discord_guild.text().strip()
        settings.discord.channel = self._discord_channel.text().strip()

        try:
            self.core.reconfigure_source()
        except Exception as exc:
            # A fonte antiga continua valendo — `reconfigure_source` monta a
            # nova antes de descartar a atual. Restaurar o valor evita que a
            # tela mostre uma escolha que não está em vigor.
            telemetry.source = previous_source
            self._save_status.setText(f"Salvo, mas a fonte não trocou: {exc}")
            self._save_status.setStyleSheet(f"color: {self.theme.palette.red};")
            return

        self._save_status.setText(
            "Salvo e aplicado. O Discord vale ao reabrir o programa."
        )
        self._save_status.setStyleSheet(f"color: {self.theme.palette.green};")

    def close_page(self) -> None:
        self._pool.waitForDone(2000)


def _boolean(value: bool) -> str:
    return "true" if value else "false"
