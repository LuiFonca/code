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
