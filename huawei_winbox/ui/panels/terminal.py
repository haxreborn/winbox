"""Terminal integrado — como o *New Terminal* do WinBox.

Para quem quiser ver ou digitar o CLI diretamente, sem abandonar a
interface gráfica. Cada comando digitado é enviado pela mesma sessão
SSH usada pelos painéis.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QLabel,
)

from ..worker import run_async


class TerminalPanel(QWidget):
    title = "Terminal"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._history: list[str] = []
        self._hist_pos = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlainText(
            f"Conectado a {session.hostname} ({session.host}).\n"
            "Digite um comando VRP e pressione Enter.\n")
        root.addWidget(self.output)

        line = QHBoxLayout()
        line.addWidget(QLabel(f"<{session.hostname}>"))
        self.input = QLineEdit()
        self.input.setPlaceholderText("ex.: display current-configuration")
        self.input.returnPressed.connect(self._send)
        line.addWidget(self.input)
        root.addLayout(line)

    def keyPressEvent(self, event):
        # Histórico com setas, como num terminal de verdade.
        if event.key() == Qt.Key_Up and self._history:
            self._hist_pos = max(0, self._hist_pos - 1)
            self.input.setText(self._history[self._hist_pos])
        elif event.key() == Qt.Key_Down and self._history:
            self._hist_pos = min(len(self._history), self._hist_pos + 1)
            self.input.setText(
                self._history[self._hist_pos]
                if self._hist_pos < len(self._history) else "")
        else:
            super().keyPressEvent(event)

    def _send(self):
        command = self.input.text().strip()
        if not command:
            return
        self._history.append(command)
        self._hist_pos = len(self._history)
        self.input.clear()
        self.input.setEnabled(False)
        self.output.appendPlainText(f"\n<{self.session.hostname}> {command}")

        def done(result: str):
            self.output.appendPlainText(result)
            self.input.setEnabled(True)
            self.input.setFocus()

        def fail(message: str):
            self.output.appendPlainText(f"[erro] {message}")
            self.input.setEnabled(True)

        run_async(self.session.run, done, fail, command)
