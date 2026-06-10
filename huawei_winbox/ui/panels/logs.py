"""Painel Logs — visualização do logbuffer do equipamento."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel, QSpinBox,
)

from ...core.session import _LONG_TIMEOUT
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

        bar.addWidget(QLabel("Últimas:"))
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(20, 1024)
        self.lines_spin.setValue(200)
        self.lines_spin.setSuffix(" linhas")
        bar.addWidget(self.lines_spin)

        bar.addWidget(QLabel("Filtro:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("ex.: BGP, IFNET, SHELL...")
        self.filter_edit.returnPressed.connect(self._apply_filter)
        bar.addWidget(self.filter_edit, 1)

        self.status = QLabel("")
        bar.addWidget(self.status)
        root.addLayout(bar)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Clique em Atualizar para carregar os logs.")
        root.addWidget(self.output)

        self._raw = ""

    def refresh(self):
        self.refresh_btn.setEnabled(False)
        self.status.setText("Lendo logbuffer...")
        cmd = "display logbuffer"

        def fetch():
            return self.session.run(cmd, timeout=_LONG_TIMEOUT)

        def done(text: str):
            self._raw = text
            self._apply_filter()
            count = len(text.splitlines())
            self.refresh_btn.setEnabled(True)
            self.status.setText(
                f"{count} linha(s)" if text.strip()
                else "Nenhum log no buffer — verifique se o logbuffer está habilitado.")
            if not text.strip():
                self.output.setPlainText(
                    "Sem logs no buffer.\n\n"
                    "Verifique com: display info-center\n"
                    "Habilite com: info-center logbuffer")

        def fail(message: str):
            self.output.setPlainText(f"[erro] {message}")
            self.refresh_btn.setEnabled(True)
            self.status.setText("")

        run_async(fetch, done, fail)

    def _apply_filter(self):
        needle = self.filter_edit.text().strip().lower()
        lines = self._raw.splitlines()
        # Limita ao número de linhas selecionado
        lines = lines[-self.lines_spin.value():]
        if needle:
            lines = [ln for ln in lines if needle in ln.lower()]
        self.output.setPlainText("\n".join(lines))
        # Rola para o final
        self.output.verticalScrollBar().setValue(
            self.output.verticalScrollBar().maximum())
