"""
Ponto de entrada do GT7 Coach.

Para rodar em desenvolvimento:
    python3 main.py

Para gerar o .exe (Windows), veja instruções no README.md deste projeto.
"""

import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HANNA GT7 AI")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
