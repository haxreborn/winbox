"""Ponto de entrada do HuaweiBox.

Uso:
    python -m huawei_winbox.main
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.login import LoginWindow
from .ui.mainwindow import MainWindow
from .ui.style import WINBOX_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HuaweiBox")
    app.setStyleSheet(WINBOX_QSS)

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
