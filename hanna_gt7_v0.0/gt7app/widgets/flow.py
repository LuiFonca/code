"""
Layout que quebra linha quando o que ele carrega não cabe.

Existe por um defeito concreto: o cabeçalho de página empilhava os seletores
numa linha só de `QHBoxLayout`, e a largura mínima dele era a **soma** de todos.
A Análise de stint, com quatro combos, pedia 1362 px só de cabeçalho — mais do
que a janela oferece — e a página nascia cortada, com o último seletor
inalcançável porque a rolagem horizontal estava desligada.

Uma linha de `QHBoxLayout` não tem como resolver isso: ou os itens encolhem até
ficarem ilegíveis, ou transbordam. Quebrando linha, a largura mínima passa a ser
a do **item mais largo** em vez da soma, e a página cabe em qualquer janela
razoável sem que nada seja truncado ou escondido.

Baseado no exemplo de Flow Layout da documentação do Qt, com duas diferenças que
o caso pede: alinhamento à direita (o cabeçalho tem título à esquerda e ações à
direita, e alinhadas à esquerda elas descolariam do canto) e altura calculada
por largura, que é o que faz o `QScrollArea` reservar a altura certa depois da
quebra.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QWidget,
)


class FlowLayout(QLayout):
    """Dispõe os itens em linha, quebrando para a linha seguinte quando falta
    espaço."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        spacing: int = 8,
        align_right: bool = False,
        columns: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self._align_right = align_right
        #: Teto de itens por linha, e sinal de que eles devem ter **a mesma
        #: largura**. É o modo da grade de métricas: cinco cartões de números
        #: com larguras diferentes leem como cinco coisas diferentes, e o que
        #: eles são é a mesma coisa cinco vezes. `None` deixa cada item com a
        #: largura que pedir, que é o modo da barra de seletores.
        self._columns = columns
        self.setContentsMargins(QMargins(0, 0, 0, 0))

    # ---------- contrato do QLayout ----------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802  (API do Qt)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (API do Qt)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (API do Qt)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802  (API do Qt)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (API do Qt)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802  (API do Qt)
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802  (API do Qt)
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802  (API do Qt)
        """Tudo numa linha só — é o que o cabeçalho usa quando cabe."""
        largura = 0
        altura = 0
        for indice, item in enumerate(self._items):
            tamanho = item.sizeHint()
            largura += tamanho.width() + (self._spacing if indice else 0)
            altura = max(altura, tamanho.height())
        return QSize(largura, altura)

    def minimumSize(self) -> QSize:  # noqa: N802  (API do Qt)
        """O **item mais largo**, e não a soma deles.

        É a linha que conserta o defeito: com a soma, acrescentar um seletor a
        mais empurrava a largura mínima da página para além da janela e o
        conteúdo passava a ser cortado sem aviso.
        """
        largura = 0
        altura = 0
        for item in self._items:
            tamanho = item.minimumSize()
            largura = max(largura, tamanho.width())
            altura = max(altura, tamanho.height())
        return QSize(largura, altura)

    # ---------- disposição ----------

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        """Distribui os itens e devolve a altura total ocupada.

        Com `apply=False` só mede — é o que `heightForWidth` precisa, e medir
        sem mexer nas geometrias é o que impede o layout de se reorganizar
        durante uma consulta.
        """
        margens = self.contentsMargins()
        util = rect.adjusted(
            margens.left(), margens.top(), -margens.right(), -margens.bottom()
        )
        if not self._items:
            return margens.top() + margens.bottom()

        linhas = self._split_into_rows(util.width())

        y = util.y()
        for linha in linhas:
            larguras = self._widths_for(linha, util.width())
            altura_da_linha = max(
                self._height_for(item, largura)
                for item, largura in zip(linha, larguras, strict=True)
            )
            largura_da_linha = sum(larguras) + self._spacing * (len(linha) - 1)
            x = (
                util.right() - largura_da_linha + 1
                if self._align_right
                else util.x()
            )
            for item, largura in zip(linha, larguras, strict=True):
                if apply:
                    item.setGeometry(
                        QRect(QPoint(x, y), QSize(largura, altura_da_linha))
                    )
                x += largura + self._spacing
            y += altura_da_linha + self._spacing

        # O último espaçamento não conta: ele fica **depois** da última linha.
        total = y - self._spacing - util.y()
        return total + margens.top() + margens.bottom()

    def _widths_for(self, linha: list[QLayoutItem], faixa: int) -> list[int]:
        """Largura de cada item da linha.

        No modo grade os itens dividem a faixa em partes iguais: são cartões do
        mesmo tipo, e larguras diferentes fariam "197 km/h" e "0.56 g" parecerem
        de categorias diferentes.

        Fora dele, cada um fica com o que pediu — **até o limite da faixa**. O
        teto importa porque um item pode ser ele próprio um layout que quebra
        linha: entregar-lhe a largura que ele pediu (a soma dos filhos) faria
        ele nunca quebrar e transbordar em silêncio, que foi exatamente o que
        aconteceu com a barra de seletores dentro do cabeçalho. Entregando a
        faixa, ele quebra por dentro. Quem não sabe quebrar fica com o próprio
        mínimo e a barra de rolagem cobre o resto.
        """
        if self._columns is None:
            return [
                max(
                    item.minimumSize().width(),
                    min(item.sizeHint().width(), faixa),
                )
                for item in linha
            ]
        disponivel = faixa - self._spacing * (len(linha) - 1)
        cada = max(1, disponivel // len(linha))
        return [cada] * len(linha)

    @staticmethod
    def _height_for(item: QLayoutItem, largura: int) -> int:
        """Altura do item **na largura que ele vai receber**.

        Para um item que quebra linha, `sizeHint().height()` é a altura de uma
        linha só — e reservá-la deixaria a segunda linha desenhando por cima do
        que vem abaixo.
        """
        widget = item.widget()
        if widget is not None and widget.hasHeightForWidth():
            return max(item.sizeHint().height(), widget.heightForWidth(largura))
        return item.sizeHint().height()

    def _split_into_rows(self, largura: int) -> list[list[QLayoutItem]]:
        """Agrupa os itens em linhas que caibam na largura informada.

        Um item sozinho mais largo que a faixa fica sozinho na linha dele em vez
        de ser espremido: encolher um combo abaixo do mínimo é como o nome de
        uma pista volta a aparecer truncado.
        """
        if self._columns is not None:
            # Modo grade: o número de colunas é o menor entre o teto pedido e o
            # que cabe com todos do tamanho do mais largo.
            mais_largo = max(
                (i.minimumSize().width() for i in self._items), default=1
            )
            cabem = max(
                1, (largura + self._spacing) // max(1, mais_largo + self._spacing)
            )
            por_linha = max(1, min(self._columns, cabem))

            # Linhas **equilibradas**, e não gulosas. Cinco cartões numa faixa
            # que comporta quatro dariam 4 + 1, e o cartão sozinho na segunda
            # linha se estica pela largura toda — lê como se fosse mais
            # importante que os outros quatro, o que ele não é. Repartindo em
            # 3 + 2 todos continuam do mesmo tamanho relativo.
            linhas_necessarias = -(-len(self._items) // por_linha)
            por_linha = -(-len(self._items) // linhas_necessarias)
            return [
                self._items[i : i + por_linha]
                for i in range(0, len(self._items), por_linha)
            ]

        linhas: list[list[QLayoutItem]] = []
        atual: list[QLayoutItem] = []
        ocupado = 0

        for item in self._items:
            necessario = item.sizeHint().width()
            projetado = ocupado + necessario + (self._spacing if atual else 0)
            if atual and projetado > largura:
                linhas.append(atual)
                atual = [item]
                ocupado = necessario
            else:
                atual.append(item)
                ocupado = projetado
        if atual:
            linhas.append(atual)
        return linhas


class FlowWidget(QWidget):
    """Widget que carrega um `FlowLayout` e sabe a própria altura por largura.

    O `heightForWidth` do layout não chega sozinho ao widget que o contém: quem
    consulta é o layout **pai**, e ele pergunta ao widget, não ao layout de
    dentro. Sem esta ponte, o cabeçalho quebrava a linha e continuava com a
    altura de uma linha só — a segunda ficava por baixo do conteúdo da página.
    """

    def __init__(self, *, spacing: int = 8, align_right: bool = False) -> None:
        super().__init__()
        self.flow = FlowLayout(self, spacing=spacing, align_right=align_right)
        politica = QSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        politica.setHeightForWidth(True)
        self.setSizePolicy(politica)

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (API do Qt)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802  (API do Qt)
        return self.flow.heightForWidth(width)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802  (API do Qt)
        self.flow.addWidget(widget)


def labelled(text: str, widget: QWidget, *, spacing: int = 6) -> QWidget:
    """Junta um rótulo e o campo dele num bloco que **não se separa**.

    Sem isto, a quebra de linha corta onde der: numa janela estreita a barra
    ficava com "De:" no fim de uma linha e o seletor correspondente no começo
    da outra, e um rótulo órfão é pior que um seletor sem rótulo — parece que
    falta alguma coisa, e falta mesmo, do lado errado.

    Blocos são a unidade indivisível do layout que quebra linha, e é assim que
    a barra se reorganiza sem nunca separar um par.
    """
    bloco = QWidget()
    linha = QHBoxLayout(bloco)
    linha.setContentsMargins(0, 0, 0, 0)
    linha.setSpacing(spacing)
    if text:
        linha.addWidget(QLabel(text))
    linha.addWidget(widget)
    return bloco
