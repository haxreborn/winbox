"""Terminal integrado — como o *New Terminal* do WinBox.

Acesso direto ao CLI do equipamento pela mesma sessão SSH dos painéis.
Teclas ↑/↓ navegam no histórico. Funciona para qualquer comando VRP,
incluindo os mais lentos (display current-configuration, debugging, etc.).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QLabel, QPushButton,
)

from ...core.session import _LONG_TIMEOUT
from ..worker import run_async

# Timeout generoso para o terminal — o usuário pode rodar qualquer comando.
_TERMINAL_TIMEOUT = _LONG_TIMEOUT


class TerminalPanel(QWidget):
    title = "Terminal"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._history: list[str] = []
        self._hist_pos = 0
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlainText(
            f"Conectado a {session.hostname} ({session.host})\n"
            "Use as setas ↑ ↓ para navegar no histórico de comandos.\n"
            "Comandos lentos (display current-configuration, ping, etc.) "
            f"aguardam até {int(_TERMINAL_TIMEOUT)}s.\n"
            "──────────────────────────────────────────────────────────\n")
        root.addWidget(self.output)

        bar = QHBoxLayout()
        self.prompt_label = QLabel(f"<{session.hostname}>")
        self.prompt_label.setMinimumWidth(120)
        bar.addWidget(self.prompt_label)

        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "ex.: display current-configuration | display interface brief")
        self.input.returnPressed.connect(self._send)
        # Event filter no próprio QLineEdit para capturar ↑/↓
        self.input.installEventFilter(self)
        bar.addWidget(self.input, 1)

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        bar.addWidget(self.cancel_btn)

        root.addLayout(bar)

        # Foca o campo de entrada ao abrir
        self.input.setFocus()

    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        """Intercepta ↑/↓ no QLineEdit para navegar no histórico."""
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                if self._history and self._hist_pos > 0:
                    self._hist_pos -= 1
                    self.input.setText(self._history[self._hist_pos])
                return True
            if key == Qt.Key.Key_Down:
                if self._hist_pos < len(self._history) - 1:
                    self._hist_pos += 1
                    self.input.setText(self._history[self._hist_pos])
                else:
                    self._hist_pos = len(self._history)
                    self.input.clear()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    def _send(self):
        if self._busy:
            return
        command = self.input.text().strip()
        if not command:
            return
        if command.lower() in ("exit", "quit"):
            self.output.appendPlainText(
                "\nUse o botão Cancelar ou feche a janela para encerrar.")
            self.input.clear()
            return

        self._history.append(command)
        self._hist_pos = len(self._history)
        self.input.clear()
        self._set_busy(True)
        self.output.appendPlainText(
            f"\n<{self.session.hostname}> {command}")

        def done(result: str):
            txt = result.strip() or "(sem saída)"
            self.output.appendPlainText(txt)
            self.output.appendPlainText("")
            self._set_busy(False)

        def fail(message: str):
            self.output.appendPlainText(
                f"\n[ERRO] {message}\n"
                "Dica: verifique a conexão SSH ou aguarde terminar o "
                "comando em execução.")
            self._set_busy(False)

        run_async(self.session.run, done, fail, command, _TERMINAL_TIMEOUT)

    def _cancel(self):
        """Reabilita a entrada — útil se um comando ficou preso."""
        self._set_busy(False)
        self.output.appendPlainText(
            "\n[cancelado pelo usuário — o comando pode ainda estar "
            "rodando no equipamento]")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.input.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if not busy:
            # Atualiza o label do prompt com o hostname atual
            self.prompt_label.setText(f"<{self.session.hostname}>")
            self.input.setFocus()
