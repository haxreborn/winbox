"""Painel Ferramentas — ping e traceroute a partir do equipamento
(equivalente ao *Tools* do WinBox)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel, QSpinBox,
)

from ...core.session import _LONG_TIMEOUT
from ..worker import run_async


class ToolsPanel(QWidget):
    title = "Ferramentas"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Destino:"))
        self.target = QLineEdit()
        self.target.setPlaceholderText("IP ou hostname (ex.: 8.8.8.8)")
        self.target.returnPressed.connect(self._ping)
        bar.addWidget(self.target, 1)

        bar.addWidget(QLabel("Pacotes:"))
        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(5)
        bar.addWidget(self.count)

        self.ping_btn = QPushButton("Ping")
        self.ping_btn.clicked.connect(self._ping)
        bar.addWidget(self.ping_btn)

        self.trace_btn = QPushButton("Traceroute")
        self.trace_btn.clicked.connect(self._trace)
        bar.addWidget(self.trace_btn)

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        bar.addWidget(self.cancel_btn)

        root.addLayout(bar)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlainText(
            "Os testes são executados a partir do equipamento, não do seu "
            "computador.\nInforme um destino e clique em Ping ou Traceroute.")
        root.addWidget(self.output)

    def _run_cmd(self, command: str):
        if not self.target.text().strip():
            self.output.appendPlainText("\nInforme um destino primeiro.")
            return
        self.ping_btn.setEnabled(False)
        self.trace_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.output.appendPlainText(f"\n> {command}\n")

        def done(result: str):
            txt = result.strip() or "(sem resposta do equipamento)"
            self.output.appendPlainText(txt)
            self._unlock()

        def fail(message: str):
            self.output.appendPlainText(f"[erro] {message}")
            self._unlock()

        # Timeout longo: traceroute com 30 saltos pode demorar ~60 s.
        run_async(self.session.run, done, fail, command, _LONG_TIMEOUT)

    def _unlock(self):
        self.ping_btn.setEnabled(True)
        self.trace_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def _cancel(self):
        self._unlock()
        self.output.appendPlainText(
            "[cancelado — o comando pode ainda estar rodando no equipamento]")

    def _ping(self):
        t = self.target.text().strip()
        if t:
            self._run_cmd(f"ping -c {self.count.value()} {t}")

    def _trace(self):
        t = self.target.text().strip()
        if t:
            self._run_cmd(f"tracert {t}")
