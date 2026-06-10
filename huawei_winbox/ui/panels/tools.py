"""Painel Ferramentas — ping e traceroute a partir do equipamento
(equivalente ao *Tools* do WinBox)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel, QSpinBox,
)

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
        self.count.setRange(1, 100)
        self.count.setValue(5)
        bar.addWidget(self.count)
        self.ping_btn = QPushButton("Ping")
        self.ping_btn.clicked.connect(self._ping)
        bar.addWidget(self.ping_btn)
        self.trace_btn = QPushButton("Traceroute")
        self.trace_btn.clicked.connect(self._trace)
        bar.addWidget(self.trace_btn)
        root.addLayout(bar)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlainText(
            "Os testes são executados a partir do equipamento, não do seu "
            "computador.\nInforme um destino e clique em Ping ou Traceroute.")
        root.addWidget(self.output)

    def _run(self, command: str):
        target = self.target.text().strip()
        if not target:
            self.output.appendPlainText("\nInforme um destino primeiro.")
            return
        self.ping_btn.setEnabled(False)
        self.trace_btn.setEnabled(False)
        self.output.appendPlainText(f"\n> {command}\nexecutando...")

        def done(result: str):
            self.output.appendPlainText(result)
            self._unlock()

        def fail(message: str):
            self.output.appendPlainText(f"[erro] {message}")
            self._unlock()

        run_async(self.session.run, done, fail, command)

    def _unlock(self):
        self.ping_btn.setEnabled(True)
        self.trace_btn.setEnabled(True)

    def _ping(self):
        target = self.target.text().strip()
        self._run(f"ping -c {self.count.value()} {target}")

    def _trace(self):
        target = self.target.text().strip()
        self._run(f"tracert {target}")
