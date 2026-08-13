"""
Base comum das abas de gráfico.

Telemetria e Comparação repetiam a mesma montagem: criar gráfico, registrar no
cursor sincronizado, montar cabeçalho de seção, aplicar linhas de setor. Cinco
métodos eram idênticos entre os dois arquivos.

A duplicação não era só volume — era risco de divergência. Uma correção de
cursor aplicada num arquivo e esquecida no outro produziria abas que se
comportam diferente sem motivo aparente.
"""

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QWidget

from ..widgets.widgets_chart import SyncedMiniChart


class ChartTabBase(QWidget):
    """Montagem de gráficos e cursor sincronizado.

    A subclasse define o layout e chama `add_chart` / `add_wheel_mosaic`; esta
    base cuida do registro no cursor e da aplicação das linhas de setor a todos
    os gráficos de uma vez.
    """

    def __init__(self):
        super().__init__()
        self._charts: list[SyncedMiniChart] = []

    # ---------- construção ----------

    def add_chart(self, title: str, height: int = 130) -> SyncedMiniChart:
        """Cria um gráfico já ligado ao cursor sincronizado.

        Registrar aqui, e não em cada aba, é o que garante que um gráfico novo
        nunca fique de fora da sincronização por esquecimento.
        """
        chart = SyncedMiniChart(title, height=height)
        chart.hovered_at_distance.connect(self._on_hover)
        chart.hover_left.connect(self._on_hover_left)
        self._charts.append(chart)
        return chart

    @staticmethod
    def section_header(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionHeader")
        return label

    def add_wheel_mosaic(
        self,
        labels: dict[str, str],
        colors: dict[str, str],
        unit: str = "",
        height: int = 110,
        central_widget: QWidget | None = None,
    ) -> tuple[QFrame, dict[str, SyncedMiniChart]]:
        """Grade com um gráfico por roda.

        Quatro linhas no mesmo gráfico se sobrepõem quando os valores são
        próximos — que é o caso normal. Separadas, dá para ver a assimetria
        entre lados, que é o que interessa no acerto do carro.

        Com `central_widget`, a grade abre espaço no meio para um indicador
        (usado pelo mosaico de deslizamento); sem ele, vira um 2×2 simples.
        """
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        charts: dict[str, SyncedMiniChart] = {}
        rodas = ("fl", "fr", "rl", "rr")

        if central_widget is None:
            posicoes = {"fl": (0, 0), "fr": (0, 1), "rl": (1, 0), "rr": (1, 1)}
        else:
            posicoes = {"fl": (0, 0), "fr": (0, 2), "rl": (1, 0), "rr": (1, 2)}

        for roda in rodas:
            titulo = labels[roda] + (f" ({unit})" if unit else "")
            chart = self.add_chart(titulo, height=height)
            charts[roda] = chart
            linha, coluna = posicoes[roda]
            grid.addWidget(chart, linha, coluna)

        if central_widget is not None:
            grid.addWidget(central_widget, 0, 1, 2, 1)
            grid.setColumnStretch(0, 3)
            grid.setColumnStretch(1, 2)
            grid.setColumnStretch(2, 3)

        return frame, charts

    # ---------- comportamento comum ----------

    def apply_sector_lines(self, boundaries: list[float] | None) -> None:
        """Desenha as linhas de setor em todos os gráficos.

        O widget espera pares `(posição, rótulo)`. Centralizar a conversão aqui
        evita o erro que já aconteceu uma vez: passar floats crus, o que levanta
        exceção no meio da renderização e deixa a tela pela metade.
        """
        linhas = (
            [(d, f"S{i + 1}") for i, d in enumerate(boundaries)] if boundaries else []
        )
        for chart in self._charts:
            chart.set_sector_lines(linhas)

    def _on_hover(self, x_value: float) -> None:
        for chart in self._charts:
            chart.show_crosshair(x_value)

    def _on_hover_left(self) -> None:
        for chart in self._charts:
            chart.hide_crosshair()
