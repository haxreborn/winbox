"""Painel Logs — eventos do equipamento com severidade, filtro e tempo real.

Antes era um despejo de texto com um filtro por palavra. O logbuffer do
VRP já carrega a severidade dentro do próprio código do evento
(``%%01BGP/3/STATE_CHG`` — o ``3`` é o nível), e é isso que separa um
registro de comando de rotina de um vizinho BGP caindo.

Agora os eventos vêm em tabela, coloridos por gravidade, com filtro por
nível mínimo, por módulo e por texto, contador por severidade e um modo
**Acompanhar** que relê o buffer periodicamente e mostra só o que chegou
de novo. A aba de texto puro continua ali para copiar e colar.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel, QSpinBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QFileDialog, QMessageBox, QCheckBox,
)

from ...core import parsers
from ...core.scheduler import BACKGROUND, NORMAL, Dropped
from ..worker import run_async

_LOG_TIMEOUT = 90.0

# Cor de fundo por severidade — só os níveis que merecem destaque.
_LEVEL_BG = {
    0: QColor("#f8d7da"), 1: QColor("#f8d7da"), 2: QColor("#fadbd8"),
    3: QColor("#fdecea"), 4: QColor("#fff4e0"),
}
_LEVEL_FG = {
    0: QColor("#7b1a1a"), 1: QColor("#7b1a1a"), 2: QColor("#96281b"),
    3: QColor("#c0392b"), 4: QColor("#8a6100"),
}
# Rótulo do filtro de nível mínimo (mostra o nível escolhido e os piores).
_LEVELS = [
    ("Tudo", 7), ("Informação ou pior", 6), ("Notificação ou pior", 5),
    ("Aviso ou pior", 4), ("Erro ou pior", 3), ("Crítico ou pior", 2),
]


class LogsPanel(QWidget):
    title = "Logs"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._entries: list[dict] = []
        self._seen: set[str] = set()
        self._raw = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_toolbar())

        self._tabs = QTabWidget()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Horário", "Nível", "Módulo", "Evento", "Mensagem"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self._tabs.addTab(self.table, "Eventos")

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.output.setFont(mono)
        self.output.setPlaceholderText(
            "Clique em Atualizar para carregar os logs.")
        self._tabs.addTab(self.output, "Texto puro")
        root.addWidget(self._tabs, 1)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setObjectName("logDetail")
        root.addWidget(self.detail)

        footer = QHBoxLayout()
        self.counts = QLabel("")
        footer.addWidget(self.counts)
        footer.addStretch(1)
        self.status = QLabel("")
        footer.addWidget(self.status)
        root.addLayout(footer)

        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_tick)

        self.refresh()

    # ================================================================== #
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)

        self.follow_chk = QCheckBox("Acompanhar")
        self.follow_chk.setToolTip(
            "Relê o logbuffer periodicamente e marca o que chegou de novo.\n"
            "A consulta entra como trabalho de fundo: não atrasa o que você "
            "estiver fazendo em outra janela.")
        self.follow_chk.toggled.connect(self._follow_toggled)
        bar.addWidget(self.follow_chk)
        self.follow_combo = QComboBox()
        for secs in (10, 30, 60):
            self.follow_combo.addItem(f"{secs}s", secs)
        self.follow_combo.setCurrentIndex(1)
        self.follow_combo.currentIndexChanged.connect(self._follow_toggled)
        bar.addWidget(self.follow_combo)

        bar.addWidget(QLabel("Nível:"))
        self.level_combo = QComboBox()
        for label, level in _LEVELS:
            self.level_combo.addItem(label, level)
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.level_combo)

        bar.addWidget(QLabel("Módulo:"))
        self.module_combo = QComboBox()
        self.module_combo.addItem("(todos)", "")
        self.module_combo.setMinimumWidth(110)
        self.module_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.module_combo)

        bar.addWidget(QLabel("Últimas:"))
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(20, 4096)
        self.lines_spin.setValue(500)
        self.lines_spin.setSuffix(" linhas")
        self.lines_spin.valueChanged.connect(self._apply_filter)
        bar.addWidget(self.lines_spin)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Procurar no texto do evento…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_edit, 1)

        export_btn = QPushButton("Exportar…")
        export_btn.clicked.connect(self._export)
        bar.addWidget(export_btn)
        return bar

    # ================================================================== #
    # Coleta
    # ================================================================== #
    def refresh(self, *_args, background: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)
        if not background:
            self.status.setText("Lendo logbuffer…")
        session = self.session
        interval = self.follow_combo.currentData() or 30

        def fetch() -> str:
            return session.run(
                "display logbuffer", timeout=_LOG_TIMEOUT,
                priority=BACKGROUND if background else NORMAL,
                ttl=5.0, max_wait=interval * 2 if background else None,
                tag="logs")

        def done(text: str) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self._raw = text
            entries = parsers.parse_logbuffer(text)
            # Na primeira leitura tudo é inédito; destacar o buffer inteiro
            # como "novo" não diria nada. O realce começa a valer da segunda
            # leitura em diante, que é quando "novo" significa algo.
            first_load = not self._seen
            fresh = 0
            for entry in entries:
                key = entry["raw"]
                is_new = key not in self._seen
                self._seen.add(key)
                entry["new"] = is_new and not first_load
                fresh += int(is_new and not first_load)
            self._entries = entries
            self._fill_modules()
            self._apply_filter()
            self.output.setPlainText(text)
            if not text.strip():
                self.status.setText(
                    "Nenhum log no buffer — verifique com 'display "
                    "info-center' e habilite com 'info-center logbuffer'.")
                return
            when = time.strftime("%H:%M:%S")
            if background:
                self.status.setText(
                    f"{fresh} evento(s) novo(s) às {when}" if fresh
                    else f"sem novidades às {when}")
            else:
                self.status.setText(f"{len(entries)} evento(s) · lido às {when}")

        def fail(message: str) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            if background:
                self.status.setText(f"⚠ {message.splitlines()[0][:90]}")
                return
            self.output.setPlainText(f"[erro] {message}")
            self.status.setText("")

        def guarded() -> str:
            try:
                return fetch()
            except Dropped:
                return self._raw          # equipamento ocupado: mantém o que há

        run_async(guarded, done, fail)

    def _follow_toggled(self) -> None:
        if self.follow_chk.isChecked():
            self._follow_timer.start(
                (self.follow_combo.currentData() or 30) * 1000)
        else:
            self._follow_timer.stop()

    def _follow_tick(self) -> None:
        if self.isVisible():
            self.refresh(background=True)

    # ================================================================== #
    # Exibição
    # ================================================================== #
    def _fill_modules(self) -> None:
        modules = sorted({e["module"] for e in self._entries if e["module"]})
        current = self.module_combo.currentData()
        if [self.module_combo.itemData(i)
                for i in range(1, self.module_combo.count())] == modules:
            return
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        self.module_combo.addItem("(todos)", "")
        for module in modules:
            self.module_combo.addItem(module, module)
        if current:
            index = self.module_combo.findData(current)
            if index >= 0:
                self.module_combo.setCurrentIndex(index)
        self.module_combo.blockSignals(False)

    def _apply_filter(self) -> None:
        max_level = self.level_combo.currentData()
        module = self.module_combo.currentData() or ""
        needle = self.filter_edit.text().strip().lower()
        limit = self.lines_spin.value()

        visible = [e for e in self._entries
                   if e["level"] <= max_level
                   and (not module or e["module"] == module)
                   and (not needle or needle in e["raw"].lower())]
        visible = visible[-limit:]

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            values = [entry["stamp"], entry["severity"], entry["module"],
                      entry["event"], entry["message"]]
            for column, text in enumerate(values):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.UserRole, entry)
                background = _LEVEL_BG.get(entry["level"])
                if background is not None:
                    item.setBackground(background)
                else:
                    item.setBackground(Qt.transparent)
                foreground = _LEVEL_FG.get(entry["level"])
                if foreground is not None:
                    item.setForeground(foreground)
                else:
                    item.setForeground(QColor("#1c1c1c"))
                font = item.font()
                font.setBold(bool(entry.get("new")) and entry["level"] <= 4)
                item.setFont(font)
        self.table.setUpdatesEnabled(True)
        self.table.scrollToBottom()

        counters = {}
        for entry in self._entries:
            counters[entry["level"]] = counters.get(entry["level"], 0) + 1
        parts = [f"{len(visible)} de {len(self._entries)} evento(s)"]
        for level in sorted(counters):
            if level <= 4:
                parts.append(f"{counters[level]} "
                             f"{parsers.LOG_SEVERITY.get(level, level)}")
        self.counts.setText("  ·  ".join(parts))

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            self.detail.setText("")
            return
        entry = items[0].data(Qt.UserRole)
        if entry:
            self.detail.setText(entry["raw"])

    def _export(self) -> None:
        if not self._raw.strip():
            QMessageBox.information(self, self.title,
                                    "Não há logs carregados.")
            return
        stamp = time.strftime("%Y%m%d-%H%M")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Exportar logs",
            f"logs-{self.session.hostname}-{stamp}.txt",
            "Texto (*.txt);;Todos (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._raw)
        except OSError as exc:
            QMessageBox.warning(self, self.title, f"Falha ao gravar: {exc}")
            return
        self.status.setText(f"Logs exportados para {path}")

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._follow_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if self.follow_chk.isChecked() and not self._follow_timer.isActive():
            self._follow_toggled()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self._follow_timer.stop()
        self.session.scheduler.cancel("logs")
        super().closeEvent(event)
