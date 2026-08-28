"""
Folha de estilo gerada a partir dos tokens.

Também Python puro: QSS é texto, e gerá-lo sem Qt significa que a coerência
visual pode ser verificada por teste sem subir uma janela. Há um teste que
varre o QSS atrás de hexadecimais que não venham da paleta — é o equivalente
visual do teste de arquitetura que impede o núcleo de importar Qt.

Por que uma folha global e não estilo por widget
------------------------------------------------
`setStyleSheet` num widget filho quebra a herança em cascata do Qt de formas
difíceis de prever, e foi assim que a aplicação anterior acabou com cartões que
mudavam de cor conforme a ordem de montagem. Aqui a folha é aplicada **uma vez**
no `QApplication`, e os widgets só declaram `objectName` — que é o seletor.
"""

from __future__ import annotations

from .tokens import Radius, Space, Theme

# Nomes de objeto usados como seletor na folha. Constantes em vez de literais
# espalhados: um erro de digitação em `setObjectName` produz um widget sem
# estilo e nenhum aviso, que é o pior tipo de falha para depurar.
OBJ_CARD = "card"
OBJ_CARD_TITLE = "cardTitle"
OBJ_CARD_VALUE = "cardValue"
OBJ_CARD_UNIT = "cardUnit"
OBJ_SIDEBAR = "sidebar"
OBJ_NAV_BUTTON = "navButton"
OBJ_PAGE_TITLE = "pageTitle"
OBJ_PAGE_SUBTITLE = "pageSubtitle"
OBJ_SECTION_TITLE = "sectionTitle"
OBJ_TOOLBAR = "toolbar"
OBJ_STATUS_BAR = "statusBar"
OBJ_PALETTE = "commandPalette"
OBJ_PALETTE_INPUT = "commandPaletteInput"
OBJ_PALETTE_LIST = "commandPaletteList"
OBJ_BADGE = "badge"
OBJ_GHOST_BUTTON = "ghostButton"
OBJ_MONO = "mono"
OBJ_SELECTOR_NOTE = "selectorNote"


def build_stylesheet(theme: Theme) -> str:
    """QSS completo da aplicação para o tema informado."""
    p = theme.palette
    t = theme.type_scale

    return f"""
/* ---------- base ---------- */
QWidget {{
    background-color: {p.canvas};
    color: {p.text_primary};
    font-family: {t.family_ui};
    font-size: {t.body}px;
}}
QMainWindow, QDialog {{ background-color: {p.canvas}; }}
QToolTip {{
    background-color: {p.surface_overlay};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {Radius.SM.px}px;
    padding: {Space.SM.px}px;
}}

/* ---------- tipografia ---------- */
QLabel#{OBJ_PAGE_TITLE} {{
    font-size: {t.title}px;
    font-weight: 600;
    color: {p.text_primary};
}}
QLabel#{OBJ_PAGE_SUBTITLE} {{
    font-size: {t.body}px;
    color: {p.text_muted};
    background: transparent;
}}
QWidget#{OBJ_CARD} QLabel {{ background: transparent; }}
QLabel#{OBJ_SECTION_TITLE} {{
    font-size: {t.label}px;
    font-weight: 600;
    color: {p.text_muted};
    letter-spacing: 1px;
    text-transform: uppercase;
    background: transparent;
}}
QLabel#{OBJ_MONO} {{ font-family: {t.family_mono}; }}

/* ---------- cartões ---------- */
QWidget#{OBJ_CARD} {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {Radius.MD.px}px;
}}
QLabel#{OBJ_CARD_TITLE} {{
    font-size: {t.label}px;
    color: {p.text_muted};
    letter-spacing: 1px;
    text-transform: uppercase;
    background: transparent;
}}
QLabel#{OBJ_CARD_VALUE} {{
    font-family: {t.family_mono};
    font-size: {t.display}px;
    font-weight: 600;
    color: {p.text_primary};
    background: transparent;
}}
QLabel#{OBJ_CARD_UNIT} {{
    font-size: {t.body}px;
    color: {p.text_muted};
    background: transparent;
}}

/* Anotação ao lado de um seletor — o carro da volta, por exemplo. Discreta
   de propósito: é contexto do que está escolhido, não uma escolha. */
QLabel#{OBJ_SELECTOR_NOTE} {{
    color: {p.text_secondary};
    background: transparent;
    padding: 0px {Space.SM.px}px;
}}

/* ---------- navegação lateral ---------- */
QWidget#{OBJ_SIDEBAR} {{
    background-color: {p.surface};
    border-right: 1px solid {p.border};
}}
QPushButton#{OBJ_NAV_BUTTON} {{
    background-color: transparent;
    border: none;
    border-radius: {Radius.SM.px}px;
    padding: {Space.MD.px}px {Space.LG.px}px;
    text-align: left;
    font-size: {t.body}px;
    font-weight: 500;
    color: {p.text_secondary};
}}
QPushButton#{OBJ_NAV_BUTTON}:hover {{
    background-color: {p.surface_raised};
    color: {p.text_primary};
}}
QPushButton#{OBJ_NAV_BUTTON}:checked {{
    background-color: {p.accent};
    color: {p.accent_text};
    font-weight: 600;
}}

/* ---------- botões ---------- */
QPushButton {{
    background-color: {p.accent};
    color: {p.accent_text};
    border: none;
    border-radius: {Radius.SM.px}px;
    padding: {Space.SM.px}px {Space.LG.px}px;
    font-size: {t.body}px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {p.accent_hover}; }}
QPushButton:disabled {{
    background-color: {p.surface_raised};
    color: {p.text_muted};
}}
QPushButton#{OBJ_GHOST_BUTTON} {{
    background-color: transparent;
    border: 1px solid {p.border_strong};
    color: {p.text_secondary};
}}
QPushButton#{OBJ_GHOST_BUTTON}:hover {{
    background-color: {p.surface_raised};
    color: {p.text_primary};
}}

/* ---------- entradas ---------- */
QComboBox, QLineEdit, QSpinBox {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {Radius.SM.px}px;
    padding: {Space.SM.px}px {Space.MD.px}px;
    color: {p.text_primary};
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {p.accent};
}}
/* A seta é desenhada à mão porque a nativa não sobrevive ao tema: estilizar o
   QComboBox tira o desenho nativo do macOS, e sem `image` não entra nada no
   lugar — o combo fica indistinguível de um campo de texto. O campo de pista
   parecia então exigir que se soubesse o nome de cor, havendo 105 numa lista
   logo ali. O triângulo sai de bordas transparentes num elemento de tamanho
   zero: sem arquivo de imagem, que exigiria empacotar um recurso binário só
   para isto e desenhá-lo numa cor fixa que brigaria com o tema. */
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    width: 0px;
    height: 0px;
    margin-right: 8px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p.text_muted};
}}
QComboBox::down-arrow:on {{ border-top-color: {p.accent}; }}
QComboBox QAbstractItemView {{
    background-color: {p.surface_overlay};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {Radius.SM.px}px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
    outline: none;
}}

/* ---------- tabelas e listas ---------- */
QTableWidget, QTableView, QListWidget {{
    background-color: {p.surface};
    alternate-background-color: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {Radius.MD.px}px;
    gridline-color: {p.border};
    color: {p.text_primary};
    outline: none;
}}
QTableWidget::item, QListWidget::item {{
    padding: {Space.SM.px}px;
    border: none;
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background-color: {p.accent};
    color: {p.accent_text};
}}
QHeaderView::section {{
    background-color: {p.surface_raised};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: {Space.SM.px}px;
    font-size: {t.label}px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ---------- rolagem ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 30px;
}}

/* ---------- paleta de comandos ---------- */
QFrame#{OBJ_PALETTE} {{
    background-color: {p.surface_overlay};
    border: 1px solid {p.border_strong};
    border-radius: {Radius.LG.px}px;
}}
QLineEdit#{OBJ_PALETTE_INPUT} {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p.border};
    border-radius: 0;
    padding: {Space.MD.px}px {Space.LG.px}px;
    font-size: {t.heading}px;
    color: {p.text_primary};
}}
QListWidget#{OBJ_PALETTE_LIST} {{
    background-color: transparent;
    border: none;
    border-radius: 0;
}}
QListWidget#{OBJ_PALETTE_LIST}::item {{
    padding: {Space.MD.px}px {Space.LG.px}px;
    border-radius: {Radius.SM.px}px;
}}

/* ---------- diversos ---------- */
QLabel#{OBJ_BADGE} {{
    background-color: {p.surface_raised};
    color: {p.text_secondary};
    border: 1px solid {p.border};
    border-radius: {Radius.PILL.px}px;
    padding: {Space.XS.px}px {Space.MD.px}px;
    font-size: {t.label}px;
    font-weight: 600;
}}
QWidget#{OBJ_TOOLBAR} {{ background-color: transparent; }}
QLabel#{OBJ_STATUS_BAR} {{
    color: {p.text_muted};
    font-size: {t.label}px;
    font-family: {t.family_mono};
}}
QSplitter::handle {{ background-color: {p.border}; }}
"""
