"""
Tokens de design — a fonte única da verdade visual.

**Python puro, sem Qt.** Isso não é acidente: os tokens são dados, e mantê-los
livres de Qt permite testá-los, gerar a folha de estilo sem interface gráfica e
— mais adiante — reaproveitar a mesma paleta num relatório HTML ou numa
mensagem do Discord, que são saídas previstas no briefing.

A regra de uso é simples e vale para todo o `gt7app`: **nenhum widget escreve um
valor hexadecimal, um espaçamento ou um tamanho de fonte literal.** Se um valor
novo é necessário, ele nasce aqui. A aplicação anterior espalhou a paleta por
`styles.py`, `window.py` e cada widget de gráfico, e o resultado foi cinco tons
de cinza levemente diferentes que ninguém escolheu.

Identidade
----------
A base é sóbria como a da Apple — superfícies escuras estratificadas, sem preto
absoluto, hierarquia tipográfica forte, muito espaço em branco — mas o
vocabulário de cor é de automobilismo. As cores de tempo (`PURPLE`, `GREEN`,
`YELLOW`) são as da torre de cronometragem, e não uma escolha estética: um
piloto lê "roxo" como *melhor da sessão* sem precisar de legenda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SequentialRamp:
    """Escala sequencial de **uma cor só**, do menor valor ao maior.

    Serve à magnitude — velocidade ao longo do traçado, no caso. Uma cor só com
    claridade monotônica, nunca arco-íris: num arco-íris o leitor não sabe se
    verde é mais ou menos que laranja sem consultar a legenda, e a ordem deixa
    de estar na cor.

    Os passos vêm de uma escala documentada e **validada**, não escolhidos a
    olho. As duas versões (clara e escura) foram verificadas contra a superfície
    de cada tema para claridade monotônica, separação visível entre passos e
    contraste mínimo da ponta que encosta no fundo.

    Uma diferença deliberada em relação a um mapa de calor comum: normalmente a
    ponta "perto de zero" pode recuar até sumir no fundo, porque zero significa
    "sem dado". Aqui não — a ponta é a **curva lenta**, exatamente onde o piloto
    olha. Por isso a escala fica numa faixa em que a linha continua visível na
    volta inteira, ao custo de menos alcance dinâmico.
    """

    steps: tuple[str, ...]

    def at(self, ratio: float) -> str:
        """Cor na posição informada (0 = menor valor, 1 = maior).

        Interpola em sRGB entre passos vizinhos. Não é o espaço perceptualmente
        correto para grandes saltos, mas os passos são da mesma matiz e ficam
        próximos — a diferença contra OKLab aqui é invisível, e a conta cabe no
        laço de pintura.
        """
        clamped = min(max(ratio, 0.0), 1.0)
        if len(self.steps) < 2:
            return self.steps[0]

        position = clamped * (len(self.steps) - 1)
        index = min(int(position), len(self.steps) - 2)
        blend = position - index
        return _mix(self.steps[index], self.steps[index + 1], blend)


def _mix(start: str, end: str, ratio: float) -> str:
    """Mistura dois hexadecimais em sRGB."""
    a = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(x + (y - x) * ratio) for x, y in zip(a, b, strict=True))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


@dataclass(frozen=True, slots=True)
class Palette:
    """Cores de um tema. Todas explícitas — nada herda do sistema.

    Herdar do sistema foi um bug real na aplicação anterior: em tema claro do
    sistema operacional, o texto saía escuro sobre fundo escuro e o seletor de
    pista ficava ilegível.
    """

    # Superfícies, do fundo para a frente. A estratificação é o que dá
    # profundidade sem usar sombra pesada ou borda grossa.
    canvas: str
    surface: str
    surface_raised: str
    surface_overlay: str

    border: str
    border_strong: str

    text_primary: str
    text_secondary: str
    text_muted: str

    accent: str
    accent_hover: str
    accent_text: str

    # Semântica de tempo, herdada da torre de cronometragem.
    purple: str
    """Melhor absoluto — da sessão ou do histórico da pista."""

    green: str
    """Melhor pessoal / mais rápido que a referência."""

    yellow: str
    """Mais lento que a referência."""

    red: str
    """Erro, perda relevante, desconexão."""

    orange: str
    """Atenção — degradação, temperatura fora de faixa."""

    # Uma cor por roda, estável em toda a aplicação: o mesmo pneu tem a mesma
    # cor no mosaico de temperatura, no gráfico de escorregamento e no mapa.
    wheel_fl: str
    wheel_fr: str
    wheel_rl: str
    wheel_rr: str

    # Canais de telemetria. Fixar aqui evita que dois gráficos desenhem
    # "velocidade" em cores diferentes.
    channel_speed: str
    channel_throttle: str
    channel_brake: str
    channel_gear: str
    channel_steering: str
    channel_slope: str
    """Inclinação da pista. Cor própria, e não a da guinada: os dois são
    canais com sinal e ficam perto um do outro na página — desenhados no
    mesmo amarelo, ler qual é qual passa a depender de contar quadros."""

    speed_ramp: SequentialRamp
    """Escala de magnitude para o mapa de calor de velocidade.

    A matiz é a mesma de `channel_speed` de propósito: velocidade já é azul nos
    gráficos, e trocar de cor no mapa obrigaria o leitor a reaprender.
    """

    def wheel(self, code: str) -> str:
        """Cor da roda pelo código (`fl`, `fr`, `rl`, `rr`)."""
        return str(getattr(self, f"wheel_{code}"))

    def channel(self, name: str) -> str:
        """Cor de um canal de telemetria, com queda para o acento."""
        return str(getattr(self, f"channel_{name}", self.accent))

    def delta(self, value: float) -> str:
        """Verde quando à frente, amarelo quando atrás.

        Amarelo e não vermelho: perder três décimos é o estado normal de uma
        volta de treino, não um erro. O vermelho fica reservado para o que
        exige ação — e um painel que pisca vermelho o tempo todo é um painel que
        se aprende a ignorar.
        """
        return self.green if value <= 0 else self.yellow


DARK = Palette(
    canvas="#0e1014",
    surface="#16191f",
    surface_raised="#1c2028",
    surface_overlay="#232733",
    border="#252a34",
    border_strong="#333947",
    text_primary="#f0f1f4",
    text_secondary="#b6bac4",
    text_muted="#71767f",
    accent="#4f7cff",
    accent_hover="#6b91ff",
    accent_text="#ffffff",
    purple="#b47cff",
    green="#3ddc84",
    yellow="#f2c94c",
    red="#ff5c5c",
    orange="#f2994a",
    wheel_fl="#4f7cff",
    wheel_fr="#3ddc84",
    wheel_rl="#f2994a",
    wheel_rr="#b47cff",
    channel_speed="#4f7cff",
    channel_throttle="#3ddc84",
    channel_brake="#ff5c5c",
    channel_gear="#b47cff",
    channel_steering="#f2c94c",
    channel_slope="#5ad1c8",
    # No tema escuro a ponta lenta é a escura (encosta no fundo) e a rápida é a
    # clara. A escala é **escolhida** para o escuro, não invertida do claro.
    speed_ramp=SequentialRamp(
        ("#184f95", "#256abf", "#3987e5", "#86b6ef", "#cde2fb")
    ),
)

LIGHT = Palette(
    canvas="#f5f6f8",
    surface="#ffffff",
    surface_raised="#ffffff",
    surface_overlay="#ffffff",
    border="#e2e5ea",
    border_strong="#c9ced8",
    text_primary="#14171c",
    text_secondary="#4a505c",
    text_muted="#828997",
    accent="#2f5fe0",
    accent_hover="#1f4bc4",
    accent_text="#ffffff",
    purple="#7b3fd4",
    green="#12a35c",
    yellow="#b8860b",
    red="#d93636",
    orange="#c2701a",
    wheel_fl="#2f5fe0",
    wheel_fr="#12a35c",
    wheel_rl="#c2701a",
    wheel_rr="#7b3fd4",
    channel_speed="#2f5fe0",
    channel_throttle="#12a35c",
    channel_brake="#d93636",
    channel_gear="#7b3fd4",
    channel_steering="#b8860b",
    channel_slope="#0f8a80",
    speed_ramp=SequentialRamp(
        ("#86b6ef", "#5598e7", "#2a78d6", "#184f95", "#0d366b")
    ),
)


class Space(StrEnum):
    """Escala de espaçamento, base 4. Usar a escala em vez de números soltos é
    o que faz telas diferentes parecerem o mesmo produto."""

    XS = "4"
    SM = "8"
    MD = "12"
    LG = "16"
    XL = "24"
    XXL = "32"

    @property
    def px(self) -> int:
        return int(self.value)


class Radius(StrEnum):
    SM = "6"
    MD = "10"
    LG = "14"
    PILL = "999"

    @property
    def px(self) -> int:
        return int(self.value)


@dataclass(frozen=True, slots=True)
class TypeScale:
    """Escala tipográfica.

    Os números de telemetria usam fonte **monoespaçada tabular**: sem isso, um
    valor que oscila entre 99 e 100 km/h faz o texto inteiro tremer, porque os
    dígitos têm larguras diferentes. É o tipo de detalhe que ninguém elogia e
    todo mundo percebe quando falta.
    """

    display: int = 40
    title: int = 22
    heading: int = 16
    body: int = 13
    label: int = 11
    micro: int = 10

    family_ui: str = "'SF Pro Display', 'Segoe UI', 'Inter', system-ui, sans-serif"
    family_mono: str = "'SF Mono', 'JetBrains Mono', 'Cascadia Mono', 'Menlo', monospace"


TYPE = TypeScale()


@dataclass(frozen=True, slots=True)
class Theme:
    """Um tema completo: paleta + escalas."""

    name: str
    palette: Palette
    type_scale: TypeScale = field(default=TYPE)

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


DARK_THEME = Theme(name="dark", palette=DARK)
LIGHT_THEME = Theme(name="light", palette=LIGHT)

THEMES: dict[str, Theme] = {"dark": DARK_THEME, "light": LIGHT_THEME}

DEFAULT_THEME = "dark"


def get_theme(name: str | None = None) -> Theme:
    """Tema pelo nome, com queda para o padrão.

    Nome desconhecido não é erro: um `.env` com um tema que não existe mais deve
    subir a aplicação com o tema padrão, não impedir o piloto de usá-la.
    """
    if name is None:
        return THEMES[DEFAULT_THEME]
    return THEMES.get(name.strip().lower(), THEMES[DEFAULT_THEME])
