"""Ponto de entrada do HuaweiBox.

Uso:
    python -m huawei_winbox.main
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .ui.login import LoginWindow
from .ui.mainwindow import MainWindow
from .ui.style import build_winbox_qss


def _light_palette() -> QPalette:
    """Paleta clara fixa — sem ela, sistemas em modo escuro injetam texto
    branco nos widgets e os campos ficam ilegíveis."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#ececec"))
    p.setColor(QPalette.WindowText, QColor("#1c1c1c"))
    p.setColor(QPalette.Base, QColor("#ffffff"))
    p.setColor(QPalette.AlternateBase, QColor("#eef3fa"))
    p.setColor(QPalette.Text, QColor("#111111"))
    p.setColor(QPalette.PlaceholderText, QColor("#8c8c8c"))
    p.setColor(QPalette.Button, QColor("#e4e4e4"))
    p.setColor(QPalette.ButtonText, QColor("#1c1c1c"))
    p.setColor(QPalette.Highlight, QColor("#316ac5"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor("#fffbe8"))
    p.setColor(QPalette.ToolTipText, QColor("#1c1c1c"))
    return p


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HuaweiBox")
    # "Fusion" ignora o tema nativo do sistema (claro ou escuro) e respeita
    # integralmente a paleta e o QSS definidos aqui.
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(build_winbox_qss())

    windows: list[MainWindow] = []  # mantém referências vivas
    login = LoginWindow()

    def open_main(session):
        window = MainWindow(session)
        windows.append(window)
        window.show()

    login.connected.connect(open_main)
    login.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
