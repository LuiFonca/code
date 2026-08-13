"""
Saúde da sessão: o que só aparece depois de horas ligado.

Por que isto vive dentro do app
--------------------------------
A primeira versão desta medição era uma ferramenta separada, que abria a porta
33740 por conta própria. Não funciona: UDP unicast entrega o pacote a **um**
socket, não a todos os que estão escutando. Com o app aberto, a ferramenta
rouba o fluxo em vez de observá-lo — e mede uma sessão que o app não está
recebendo. `SO_REUSEPORT` não muda isso; ele permite o segundo `bind`, e é
justamente aí que está a armadilha: nada falha, o app simplesmente para de
receber.

Medir de dentro resolve o problema pela raiz. O app já recebe cada pacote; o
coletor apenas observa o que passa. Nenhuma porta a mais, nenhuma disputa, e a
medição é exatamente do fluxo que o app usou.

O que se mede
-------------
Coisas que a tela não mostra e que uma sessão longa degrada em silêncio:
taxa de pacotes, interrupções no fluxo, e se a orientação do carro chega
válida (sem ela o ângulo de deriva fica com buracos).

Custo: o coletor roda a ~60 Hz. Por isso ele só soma contadores — nada de
lista por pacote, nada de I/O. A medição não pode ser a fonte do problema que
veio medir.
"""

import time
from dataclasses import dataclass

from ..events.event_bus import EventBus
from ..events.events import ConnectionStateChanged, LapCompleted, TelemetryReceived

# O GT7 transmite a ~60 Hz. Abaixo disto há perda de pacote no caminho.
TAXA_ESPERADA_HZ = 60.0
TAXA_MINIMA_ACEITAVEL_HZ = 50.0

# Silêncio acima disto conta como interrupção: muito além do intervalo normal
# (~16 ms) e curto o bastante para o piloto sentir.
BURACO_S = 0.5

# Abaixo desta fração de amostras com orientação válida, o ângulo de deriva
# sai com lacunas e não se sustenta como leitura.
ORIENTACAO_MINIMA_PCT = 95.0

# Menos que isto não é sessão longa, é teste de conexão.
VOLTAS_MINIMAS = 5


@dataclass(slots=True)
class Veredito:
    """Uma conclusão isolada do relatório."""

    ok: bool
    titulo: str
    detalhe: str = ""


class SessionHealth:
    """Contadores da sessão em andamento.

    Assina o barramento e observa; não interfere em nada. Um `reset` a cada
    conexão nova para que o relatório fale da sessão atual, e não da soma de
    tudo desde que o app abriu.
    """

    def __init__(self, event_bus: EventBus, relogio=time.monotonic):
        self._bus = event_bus
        # Relógio injetável: sem isso, testar taxa e buracos exigiria esperar
        # segundos de verdade.
        self._agora = relogio
        self.reset()

        self._on_telemetry = self._registrar
        self._on_lap = lambda _e: self._contar_volta()
        self._on_state = self._mudou_estado
        self._bus.subscribe(TelemetryReceived, self._on_telemetry)
        self._bus.subscribe(LapCompleted, self._on_lap)
        self._bus.subscribe(ConnectionStateChanged, self._on_state)

    def dispose(self) -> None:
        self._bus.unsubscribe(TelemetryReceived, self._on_telemetry)
        self._bus.unsubscribe(LapCompleted, self._on_lap)
        self._bus.unsubscribe(ConnectionStateChanged, self._on_state)

    def reset(self) -> None:
        self.amostras = 0
        self.voltas = 0
        self.buracos = 0
        self.maior_buraco_s = 0.0
        self.orientacao_ok = 0
        self.orientacao_nula = 0
        self.pausado = 0
        self.fora_de_pista = 0
        self.quedas = 0
        self._primeira: float | None = None
        self._ultima: float | None = None

    # ---------- coleta ----------

    def _mudou_estado(self, evento) -> None:
        """Uma reconexão zera a contagem; uma queda entra no relatório.

        Sem zerar, o intervalo entre desconectar e reconectar viraria um
        "buraco" gigante e afundaria a taxa média de uma sessão que estava
        perfeita.
        """
        estado = getattr(evento, "state", "")
        if estado in ("recebendo", "conectando"):
            if self._ultima is not None:
                self.quedas += 1
            self._ultima = None
        elif estado in ("desconectado", "sem_sinal", "erro"):
            self._ultima = None

    def _contar_volta(self) -> None:
        self.voltas += 1

    def _registrar(self, evento) -> None:
        agora = self._agora()
        if self._ultima is not None:
            intervalo = agora - self._ultima
            if intervalo > BURACO_S:
                self.buracos += 1
                self.maior_buraco_s = max(self.maior_buraco_s, intervalo)
        elif self._primeira is None:
            self._primeira = agora
        self._ultima = agora
        self.amostras += 1

        frame = getattr(evento, "frame", None)
        if frame is None:
            # Sem o DTO cru não há orientação para conferir. Acontece em
            # caminhos internos que republicam pontos; não é erro.
            return

        # A norma do quaternion diz se a orientação veio de fato. Zeros
        # significam campo ausente, não carro alinhado.
        norma = (
            getattr(frame, "rotation_i", 0.0) ** 2
            + getattr(frame, "rotation_j", 0.0) ** 2
            + getattr(frame, "rotation_k", 0.0) ** 2
            + getattr(frame, "rotation_w", 0.0) ** 2
        ) ** 0.5
        if norma > 0.5:
            self.orientacao_ok += 1
        else:
            self.orientacao_nula += 1

        if getattr(frame, "is_paused", False):
            self.pausado += 1
        if not getattr(frame, "is_on_track", True):
            self.fora_de_pista += 1

    # ---------- leitura ----------

    @property
    def duracao_s(self) -> float:
        if self._primeira is None or self._ultima is None:
            return 0.0
        return self._ultima - self._primeira

    @property
    def taxa_hz(self) -> float:
        return self.amostras / self.duracao_s if self.duracao_s > 0 else 0.0

    @property
    def orientacao_pct(self) -> float:
        total = self.orientacao_ok + self.orientacao_nula
        return self.orientacao_ok / total * 100.0 if total else 0.0

    def vereditos(self) -> list[Veredito]:
        """As conclusões, uma a uma. Vazio quando não há amostra suficiente."""
        if self.amostras < 2:
            return []

        saida = []

        if self.taxa_hz >= TAXA_MINIMA_ACEITAVEL_HZ:
            saida.append(
                Veredito(True, f"Taxa de {self.taxa_hz:.0f} Hz",
                         "dentro do esperado (~60 Hz)")
            )
        else:
            saida.append(
                Veredito(
                    False, f"Taxa de {self.taxa_hz:.0f} Hz",
                    f"abaixo de {TAXA_MINIMA_ACEITAVEL_HZ:.0f} Hz — há perda de "
                    "pacote no caminho. Cabo no lugar de Wi-Fi resolve na "
                    "maioria dos casos.",
                )
            )

        if self.buracos == 0:
            saida.append(Veredito(True, "Fluxo contínuo", "nenhuma interrupção"))
        else:
            saida.append(
                Veredito(
                    False, f"{self.buracos} interrupções no fluxo",
                    f"a maior de {self.maior_buraco_s:.1f}s. Uma interrupção no "
                    "meio da volta a corrompe: as amostras pulam um trecho e a "
                    "distância acumulada sai errada.",
                )
            )

        pct = self.orientacao_pct
        if pct >= ORIENTACAO_MINIMA_PCT:
            saida.append(
                Veredito(True, f"Orientação válida em {pct:.1f}% das amostras",
                         "o ângulo de deriva é confiável nesta sessão")
            )
        else:
            saida.append(
                Veredito(
                    False, f"Orientação válida em apenas {pct:.1f}% das amostras",
                    "o ângulo de deriva vai aparecer com lacunas.",
                )
            )

        if self.voltas >= VOLTAS_MINIMAS:
            saida.append(
                Veredito(True, f"{self.voltas} voltas gravadas",
                         "amostra suficiente para validar a sessão")
            )
        else:
            saida.append(
                Veredito(
                    False, f"Apenas {self.voltas} volta(s) gravada(s)",
                    f"para valer como validação de sessão longa, rode ao menos "
                    f"{VOLTAS_MINIMAS}.",
                )
            )

        return saida

    @property
    def aprovada(self) -> bool:
        vereditos = self.vereditos()
        return bool(vereditos) and all(v.ok for v in vereditos)

    def relatorio(self) -> str:
        """Relatório em texto, pronto para exibir ou colar numa mensagem."""
        linhas = ["RELATÓRIO DE SESSÃO", "=" * 52]

        if self.amostras < 2:
            linhas.append("")
            linhas.append("Nenhuma telemetria recebida ainda nesta sessão.")
            linhas.append("Conecte, entre numa sessão do GT7 e rode algumas voltas.")
            return "\n".join(linhas)

        linhas += [
            f"  duração            : {self.duracao_s / 60:.1f} min",
            f"  amostras recebidas : {self.amostras}",
            f"  taxa média         : {self.taxa_hz:.1f} Hz",
            f"  interrupções       : {self.buracos} "
            f"(maior: {self.maior_buraco_s:.1f}s)",
            f"  quedas de conexão  : {self.quedas}",
            f"  voltas gravadas    : {self.voltas}",
            f"  amostras pausado   : {self.pausado}",
            f"  amostras fora da pista: {self.fora_de_pista}",
            f"  orientação válida  : {self.orientacao_ok}",
            f"  orientação nula    : {self.orientacao_nula}",
            "",
            "VEREDITO",
        ]

        for v in self.vereditos():
            marca = "[ok] " if v.ok else "[!]  "
            linhas.append(f"  {marca}{v.titulo}")
            if v.detalhe:
                linhas.append(f"        {v.detalhe}")

        linhas.append("")
        linhas.append(
            "  SESSÃO LONGA VALIDADA." if self.aprovada
            else "  Há pendências acima."
        )
        return "\n".join(linhas)
