"""Base comum dos painéis (Interfaces, BGP, OSPF, ...).

Cada painel é uma janelinha interna estilo WinBox: barra de botões em
cima, tabela embaixo. As ações de configuração passam sempre pela
pré-visualização de CLI antes de irem ao equipamento.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QLabel, QMessageBox, QHeaderView, QComboBox,
)

from ..command_preview import CommandPreviewDialog
from ..worker import run_async


class BasePanel(QWidget):
    """Painel com tabela, botão Atualizar e infraestrutura de envio."""

    title = "Painel"
    columns: list[str] = []

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self.toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        self.toolbar.addWidget(self.refresh_btn)

        # Auto-atualização em tempo real, com intervalo escolhido pelo
        # usuário. O timer só dispara um novo fetch se o anterior já
        # terminou (refresh ignora quando _busy).
        self.auto_combo = QComboBox()
        self.auto_combo.addItem("Auto: desligado", 0)
        for secs in (5, 10, 30, 60):
            self.auto_combo.addItem(f"Auto: {secs}s", secs)
        self.auto_combo.currentIndexChanged.connect(self._auto_changed)
        self.toolbar.addWidget(self.auto_combo)
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self.refresh)

        self.build_toolbar(self.toolbar)
        self.toolbar.addStretch(1)
        self.status_label = QLabel("")
        self.toolbar.addWidget(self.status_label)
        root.addLayout(self.toolbar)

        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table)

        self.refresh()

    # ----- a serem definidos pelas subclasses ------------------------- #
    def build_toolbar(self, layout: QHBoxLayout) -> None:
        """Adiciona botões específicos do painel."""

    def fetch(self) -> list[dict]:
        """Roda no worker: consulta o equipamento e retorna as linhas."""
        return []

    def row_values(self, row: dict) -> list[str]:
        """Converte um dicionário do fetch() em células da tabela."""
        return []

    # ----- infraestrutura --------------------------------------------- #
    def _auto_changed(self):
        secs = self.auto_combo.currentData() or 0
        if secs:
            self._auto_timer.start(secs * 1000)
            self.refresh()
        else:
            self._auto_timer.stop()

    def hideEvent(self, event):
        # Janela fechada/minimizada: para de consultar o equipamento.
        self._auto_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        # Reativa o timer se o usuário deixou o auto ligado.
        secs = self.auto_combo.currentData() or 0
        if secs and not self._auto_timer.isActive():
            self._auto_timer.start(secs * 1000)
        super().showEvent(event)

    def refresh(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando equipamento...")
        run_async(self.fetch, self._on_rows, self._on_error)

    def _on_rows(self, rows: list[dict]):
        self._rows = rows
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, value in enumerate(self.row_values(row)):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.table.setItem(r, c, item)
        self._set_busy(False, f"{len(rows)} item(ns)")

    def _on_error(self, message: str):
        self._set_busy(False, "")
        QMessageBox.warning(self, self.title, message)

    def _set_busy(self, busy: bool, text: str):
        self._busy = busy
        self.refresh_btn.setEnabled(not busy)
        self.status_label.setText(text)

    def selected_row(self) -> dict | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def apply_commands(self, commands: list[str],
                       refresh_after: bool = True,
                       system_view: bool = True):
        """Mostra a pré-visualização e, se confirmado, envia via SSH."""
        two_stage = system_view and getattr(self.session, "two_stage", False)
        if not CommandPreviewDialog.confirm(commands, self,
                                            two_stage=two_stage,
                                            system_view=system_view):
            return
        self._set_busy(True, "Enviando configuração...")

        def send():
            if system_view:
                return self.session.configure(commands)
            return self.session.run_many(commands)

        def done(output: str):
            self._set_busy(False, "Configuração aplicada")
            if "Error" in output or "error" in output:
                QMessageBox.warning(
                    self, self.title,
                    "O equipamento reportou um problema:\n\n" + output)
            if refresh_after:
                self.refresh()

        run_async(send, done, self._on_error)
