"""Painel Logs — visualização do logbuffer do equipamento
(equivalente ao *Log* do WinBox)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel,
)

from ..worker import run_async


class LogsPanel(QWidget):
    title = "Logs"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(QLabel("Filtro:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("ex.: BGP, IFNET, SHELL...")
        self.filter_edit.returnPressed.connect(self._apply_filter)
        bar.addWidget(self.filter_edit)
        self.status = QLabel("")
        bar.addWidget(self.status)
        root.addLayout(bar)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        root.addWidget(self.output)

        self._raw = ""
        self.refresh()

    def refresh(self):
        self.refresh_btn.setEnabled(False)
        self.status.setText("Lendo logbuffer...")

        def done(text: str):
            self._raw = text
            self._apply_filter()
            self.refresh_btn.setEnabled(True)
            self.status.setText("")

        def fail(message: str):
            self.output.setPlainText(f"[erro] {message}")
            self.refresh_btn.setEnabled(True)
            self.status.setText("")

        run_async(self.session.run, done, fail, "display logbuffer")

    def _apply_filter(self):
        needle = self.filter_edit.text().strip().lower()
        if not needle:
            self.output.setPlainText(self._raw)
            return
        lines = [ln for ln in self._raw.splitlines()
                 if needle in ln.lower()]
        self.output.setPlainText("\n".join(lines))
