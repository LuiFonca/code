"""
Folha de estilo da aplicação.

Extraída da janela principal, onde era uma constante de 100 linhas no meio da
lógica de conexão. Separada, dá para ajustar a aparência sem abrir o arquivo que
cuida de threads e eventos.

Todas as cores de texto são declaradas explicitamente. Depender do padrão do
sistema produzia texto escuro sobre fundo escuro em temas claros — o combo de
pistas ficava ilegível dependendo do sistema operacional.
"""

# Paleta base
BG_APP = "#12141a"
BG_CARD = "#1a1d25"
BG_INPUT = "#1c1f27"
BG_MUTED = "#23262f"
BORDER = "#2a2e3a"
BORDER_CARD = "#23262f"

TEXT_PRIMARY = "#e8e8ec"
TEXT_SECONDARY = "#c8cad0"
TEXT_MUTED = "#6b6f7a"

ACCENT = "#4f7cff"
SUCCESS = "#3ddc84"
WARNING = "#f2c94c"
DANGER = "#ff5c5c"
ORANGE = "#f2994a"

DARK_STYLE = f"""
QMainWindow, QWidget {{
    background-color: {BG_APP};
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', Arial, sans-serif;
}}
QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 14px;
    color: {TEXT_PRIMARY};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    color: {TEXT_PRIMARY};
    min-width: 160px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    border: 1px solid {BORDER};
}}
QPushButton {{
    background-color: {ACCENT};
    border: none;
    border-radius: 6px;
    padding: 9px 20px;
    font-size: 14px;
    font-weight: 600;
    color: white;
}}
QPushButton:hover {{
    background-color: #6690ff;
}}
QPushButton:disabled {{
    background-color: {BORDER};
    color: {TEXT_MUTED};
}}
QPushButton#stopButton {{
    background-color: {BORDER};
}}
QPushButton#stopButton:hover {{
    background-color: #3a3f4d;
}}
QPushButton#dangerButton {{
    background-color: #6b2020;
}}
QPushButton#dangerButton:hover {{
    background-color: #8b2a2a;
}}
QCheckBox {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
}}
QRadioButton {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
}}
QLabel {{
    color: {TEXT_PRIMARY};
}}
#statusPill {{
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
}}
#card {{
    background-color: {BG_CARD};
    border-radius: 12px;
    border: 1px solid {BORDER_CARD};
}}
#metricValue {{
    font-size: 28px;
    font-weight: 700;
    color: #ffffff;
}}
#metricLabel {{
    font-size: 13px;
    color: {TEXT_SECONDARY};
    font-weight: 700;
    letter-spacing: 1px;
}}
#sectionHeader {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QProgressBar {{
    background-color: {BG_MUTED};
    border-radius: 4px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 4px;
}}
QTabWidget::pane {{
    border: none;
}}
QTabBar::tab {{
    background-color: transparent;
    color: #b0b3bc;
    padding: 10px 18px;
    font-size: 14px;
    font-weight: 700;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: #ffffff;
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}
QTableWidget {{
    background-color: {BG_CARD};
    alternate-background-color: #1e212a;
    color: {TEXT_PRIMARY};
    gridline-color: {BORDER_CARD};
    border: 1px solid {BORDER_CARD};
    border-radius: 8px;
}}
QHeaderView::section {{
    background-color: {BG_MUTED};
    color: {TEXT_SECONDARY};
    padding: 8px;
    border: none;
    font-weight: 700;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
"""

# Cores dos estados da conexão: (fundo, texto)
STATUS_COLORS = {
    "desconectado": (BORDER, "#9a9ea8"),
    "conectando": ("#3a3410", WARNING),
    "recebendo": ("#123a1f", SUCCESS),
    "stale": (BORDER, ORANGE),
    "sem_sinal": ("#3a2410", ORANGE),
    "erro": ("#3a1414", DANGER),
}

STATUS_LABELS = {
    "desconectado": "Desconectado",
    "conectando": "Conectando...",
    "recebendo": "● Conectado",
    "stale": "○ Sem dados",
    "sem_sinal": "Sem sinal",
    "erro": "Erro",
}
