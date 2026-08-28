"""
Contrato comum das páginas.

A navegação por páginas substitui as abas da aplicação anterior. A diferença
não é só visual: abas carregam tudo de uma vez e ficam vivas para sempre, o que
com quatro telas de telemetria significava quatro conjuntos de gráficos sendo
atualizados enquanto se olhava para um. Uma página sabe quando entra em cena
(`on_enter`) e quando sai (`on_leave`), e só trabalha enquanto está visível.

`refresh()` é separado de `on_enter()` porque nem toda atualização vem de
navegação: gravar uma volta nova precisa atualizar o histórico esteja ele
visível ou não — se estiver, imediatamente; se não, na próxima entrada.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..application import CoreApplication
from ..design.tokens import Space, Theme
from ..widgets.cards import PageHeader


class Page(QWidget):
    """Base de todas as páginas.

    Subclasses montam o conteúdo em `build()` e reagem em `refresh()`.
    """

    #: Identificador estável, usado pela navegação e pelos comandos.
    page_id: str = ""
    #: Rótulo no menu lateral.
    nav_title: str = ""
    #: Título grande no topo da página.
    title: str = ""
    subtitle: str = ""

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        super().__init__()
        self.core = core
        self.theme = theme
        self._dirty = True
        self._car_names: dict[int, str] = {}

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(
            Space.XL.px, Space.XL.px, Space.XL.px, Space.XL.px
        )
        self._root.setSpacing(Space.LG.px)

        self.header = PageHeader(self.title or self.nav_title, self.subtitle)
        self._root.addWidget(self.header)

        self.build()

    # ---------- a implementar ----------

    def build(self) -> None:
        """Monta os widgets. Chamado uma vez, na construção."""

    def refresh(self) -> None:
        """Recarrega os dados exibidos."""

    # ---------- consultas comuns ----------

    def car_name(self, car_id: int | None) -> str:
        """Nome do carro, com cache por página.

        Mora na base porque três telas fazem a mesma pergunta — Histórico,
        Análise e Comparação — e a resposta é a mesma consulta ao mesmo
        repositório. Três cópias divergiriam no travessão: "—", "?" e "" são
        três jeitos de dizer "não sei" que, lado a lado, parecem três estados
        diferentes.

        O cache não invalida: nome de carro não muda, e o `car_id` de uma volta
        gravada também não.
        """
        if car_id is None:
            return "—"
        if car_id not in self._car_names:
            car = self.core.cars.get_by_id(car_id)
            self._car_names[car_id] = car.name if car else "—"
        return self._car_names[car_id]

    # ---------- ciclo de vida ----------

    @property
    def content(self) -> QVBoxLayout:
        """Layout onde a subclasse acrescenta seu conteúdo."""
        return self._root

    def on_enter(self) -> None:
        """A página virou a visível."""
        if self._dirty:
            self.refresh()
            self._dirty = False

    def on_leave(self) -> None:
        """A página saiu de cena."""

    def invalidate(self) -> None:
        """Marca os dados como velhos.

        Se a página está visível, recarrega agora; se não, na próxima vez que
        aparecer. É o que evita recarregar cinco páginas a cada volta gravada.
        """
        if self.isVisible():
            self.refresh()
            self._dirty = False
        else:
            self._dirty = True

    def close_page(self) -> None:
        """Libera recursos (timers, assinaturas). Chamado no fechamento."""
