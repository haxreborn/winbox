"""Painel Hardware — CPU, memória, temperatura, fans, energia e transceivers."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget

from ...core import parsers
from ..worker import run_async


class HardwarePanel(QWidget):
    title = "Hardware"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)
        self.status_lbl = QLabel("")
        toolbar.addWidget(self.status_lbl)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        tabs = QTabWidget()

        self.cpu_text = QPlainTextEdit()
        self.cpu_text.setReadOnly(True)
        self.cpu_text.setObjectName("terminal")
        tabs.addTab(self.cpu_text, "CPU / Memória")

        self.hw_text = QPlainTextEdit()
        self.hw_text.setReadOnly(True)
        self.hw_text.setObjectName("terminal")
        tabs.addTab(self.hw_text, "Chassi / Fans / Energia")

        self.env_text = QPlainTextEdit()
        self.env_text.setReadOnly(True)
        self.env_text.setObjectName("terminal")
        tabs.addTab(self.env_text, "Temperatura")

        self.optical_text = QPlainTextEdit()
        self.optical_text.setReadOnly(True)
        self.optical_text.setObjectName("terminal")
        tabs.addTab(self.optical_text, "Transceivers (óptica)")

        root.addWidget(tabs)
        self.refresh()

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)
        self.status_lbl.setText("Consultando equipamento...")

        def fetch():
            results = {}
            for key, cmd in [
                ("cpu",     "display cpu-usage"),
                ("mem",     "display memory-usage"),
                ("device",  "display device"),
                ("env",     "display environment"),
                ("optical", "display transceiver all"),
            ]:
                try:
                    results[key] = self.session.run(cmd, timeout=30.0)
                except Exception as exc:
                    results[key] = f"[Erro: {exc}]"
            return results

        def done(results):
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status_lbl.setText("Atualizado")
            cpu = parsers.parse_cpu_usage(results["cpu"])
            mem = parsers.parse_memory_usage(results["mem"])
            self.cpu_text.setPlainText(
                f"CPU: {cpu}\nMemória: {mem}\n\n"
                f"=== display cpu-usage ===\n{results['cpu']}\n\n"
                f"=== display memory-usage ===\n{results['mem']}")
            self.hw_text.setPlainText(results["device"])
            self.env_text.setPlainText(results["env"])
            self.optical_text.setPlainText(results["optical"])

        def fail(msg):
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status_lbl.setText(f"Erro: {msg}")

        run_async(fetch, done, fail)
