"""Painel Alarmes — o que o equipamento está reclamando agora.

Faltava a pergunta mais direta do dia a dia: *tem alguma coisa errada
neste equipamento?* Os logs contam a história toda, inclusive o que já
foi resolvido; o alarme ativo é o que **continua** valendo.

Duas fontes, em abas:

``Ativos``
    ``display alarm active`` — o que está pendente agora, com severidade,
    identificador, desde quando e a descrição.
``Histórico de traps``
    ``display trapbuffer`` — os eventos que o equipamento enviaria (ou
    enviou) por SNMP. Formato igual ao do logbuffer, então reaproveita o
    mesmo leitor e a mesma coloração por severidade.

A janela acompanha sozinha em segundo plano e avisa quando um alarme novo
aparece, sem tirar a vez de quem está usando outra janela.
"""

from __future__ import annotations

import re
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QPlainTextEdit,
)

from ...core import parsers
from ...core.scheduler import BACKGROUND, NORMAL, Dropped
from ..worker import run_async

_TIMEOUT = 40.0
_FOLLOW_SECONDS = [30, 60, 300]

# Fundo por severidade do alarme (1 = crítico … 6 = evento).
_ALARM_BG = {
    1: QColor("#f8d7da"), 2: QColor("#fadbd8"), 3: QColor("#fff4e0"),
    4: QColor("#fffbe6"),
}
_ALARM_FG = {
    1: QColor("#7b1a1a"), 2: QColor("#96281b"), 3: QColor("#8a6100"),
    4: QColor("#7a6a20"),
}
_LEVEL_FILTER = [("Todos", 6), ("Aviso ou pior", 4), ("Menor ou pior", 3),
                 ("Maior ou pior", 2), ("Só críticos", 1)]

# Comandos tentados em ordem: o nome muda entre plataformas.
_ALARM_COMMANDS = ["display alarm active", "display alarm all",
                   "display fault-information active"]


class AlarmsPanel(QWidget):
    title = "Alarmes"
    state_key = "alarms"

    # Permite pular do alarme para a porta citada nele.
    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._alarms: list[dict] = []
        self._traps: list[dict] = []
        self._seen: set[str] = set()
        self._raw = ""
        self._supported = True

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_toolbar())

        self._tabs = QTabWidget()
        self.table = self._make_table(
            ["Severidade", "Desde", "Identificador", "Descrição"])
        self.table.itemDoubleClicked.connect(self._jump_to_interface)
        self._tabs.addTab(self.table, "Ativos")

        self.traps = self._make_table(
            ["Horário", "Nível", "Módulo", "Evento", "Mensagem"])
        self._tabs.addTab(self.traps, "Histórico de traps")

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self._tabs.addTab(self.output, "Saída bruta")
        root.addWidget(self._tabs, 1)

        footer = QHBoxLayout()
        self.summary = QLabel("")
        footer.addWidget(self.summary)
        footer.addStretch(1)
        self.status = QLabel("")
        footer.addWidget(self.status)
        root.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._follow_tick)
        self.refresh()

    @staticmethod
    def _make_table(columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        for column in range(len(columns) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(columns) - 1, QHeaderView.Stretch)
        return table

    # ================================================================== #
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)

        self.follow_chk = QCheckBox("Acompanhar")
        self.follow_chk.setToolTip(
            "Relê os alarmes periodicamente, em segundo plano, e destaca "
            "os que apareceram desde a última leitura.")
        self.follow_chk.toggled.connect(self._follow_toggled)
        bar.addWidget(self.follow_chk)
        self.follow_combo = QComboBox()
        for seconds in _FOLLOW_SECONDS:
            self.follow_combo.addItem(f"{seconds}s", seconds)
        self.follow_combo.setCurrentIndex(1)
        self.follow_combo.currentIndexChanged.connect(self._follow_toggled)
        bar.addWidget(self.follow_combo)

        bar.addWidget(QLabel("Severidade:"))
        self.level_combo = QComboBox()
        for label, level in _LEVEL_FILTER:
            self.level_combo.addItem(label, level)
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.level_combo)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Procurar na descrição…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_edit, 1)
        return bar

    # ================================================================== #
    def refresh(self, *_args, background: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)
        if not background:
            self.status.setText("Consultando alarmes…")
        session = self.session
        interval = self.follow_combo.currentData() or 60
        priority = BACKGROUND if background else NORMAL

        def query(command: str) -> str:
            try:
                return session.run(
                    command, timeout=_TIMEOUT, priority=priority, ttl=5.0,
                    max_wait=interval * 2 if background else None,
                    tag="alarms")
            except Dropped:
                raise
            except Exception:              # noqa: BLE001 — comando ausente
                return ""

        def fetch() -> dict:
            text = ""
            used = ""
            for command in _ALARM_COMMANDS:
                text = query(command)
                if text.strip() and not _looks_unsupported(text):
                    used = command
                    break
            traps = query("display trapbuffer")
            return {"alarms": text, "command": used, "traps": traps}

        def done(data: dict) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self._raw = (f"> {data['command'] or _ALARM_COMMANDS[0]}\n"
                         f"{data['alarms']}\n\n> display trapbuffer\n"
                         f"{data['traps']}")
            self.output.setPlainText(self._raw)
            self._supported = bool(data["command"])
            alarms = parsers.parse_active_alarms(data["alarms"])
            first_load = not self._seen
            fresh = 0
            for alarm in alarms:
                key = alarm["raw"]
                is_new = key not in self._seen
                self._seen.add(key)
                alarm["new"] = is_new and not first_load
                fresh += int(alarm["new"])
            self._alarms = alarms
            self._traps = parsers.parse_logbuffer(data["traps"])
            self._apply_filter()
            self._fill_traps()
            when = time.strftime("%H:%M:%S")
            if not self._supported:
                self.status.setText(
                    "Este equipamento não expõe 'display alarm active' — "
                    "veja a aba de traps e o painel de Logs.")
            elif background:
                self.status.setText(
                    f"{fresh} alarme(s) novo(s) às {when}" if fresh
                    else f"sem novidades às {when}")
            else:
                self.status.setText(f"lido às {when}")

        def fail(message: str) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status.setText(f"⚠ {message.splitlines()[0][:110]}")

        def guarded() -> dict:
            try:
                return fetch()
            except Dropped:
                return {"alarms": "", "command": "", "traps": ""}

        run_async(guarded, done, fail)

    def _follow_toggled(self) -> None:
        if self.follow_chk.isChecked():
            self._timer.start((self.follow_combo.currentData() or 60) * 1000)
        else:
            self._timer.stop()

    def _follow_tick(self) -> None:
        if self.isVisible():
            self.refresh(background=True)

    # ================================================================== #
    def _apply_filter(self) -> None:
        max_level = self.level_combo.currentData()
        needle = self.filter_edit.text().strip().lower()
        visible = [a for a in self._alarms
                   if a["level"] <= max_level
                   and (not needle or needle in a["raw"].lower())]
        self.table.setRowCount(len(visible))
        for row, alarm in enumerate(visible):
            values = [alarm["severity"], alarm["stamp"] or "—",
                      alarm["id"] or "—", alarm["description"]]
            for column, text in enumerate(values):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.UserRole, alarm)
                background = _ALARM_BG.get(alarm["level"])
                item.setBackground(background if background
                                   else Qt.transparent)
                foreground = _ALARM_FG.get(alarm["level"])
                item.setForeground(foreground if foreground
                                   else QColor("#1c1c1c"))
                font = item.font()
                font.setBold(bool(alarm.get("new")))
                item.setFont(font)

        counters: dict[int, int] = {}
        for alarm in self._alarms:
            counters[alarm["level"]] = counters.get(alarm["level"], 0) + 1
        if not self._alarms:
            self.summary.setText(
                "Nenhum alarme ativo." if self._supported else "—")
            return
        parts = [f"{len(visible)} de {len(self._alarms)} alarme(s)"]
        for level in sorted(counters):
            parts.append(f"{counters[level]} "
                         f"{parsers.ALARM_LABELS.get(level, level)}")
        self.summary.setText("  ·  ".join(parts))

    def _fill_traps(self) -> None:
        rows = self._traps[-400:]
        self.traps.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            values = [entry["stamp"], entry["severity"], entry["module"],
                      entry["event"], entry["message"]]
            for column, text in enumerate(values):
                item = self.traps.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.traps.setItem(row, column, item)
                item.setText(text)
                if entry["level"] <= 4:
                    item.setForeground(QColor("#c0392b") if entry["level"] <= 3
                                       else QColor("#8a6100"))
        self.traps.scrollToBottom()

    def _jump_to_interface(self, item) -> None:
        """Duplo clique num alarme que cita uma porta abre o gráfico dela."""
        alarm = item.data(Qt.UserRole)
        if not alarm:
            return
        match = parsers.INTERFACE_IN_TEXT.search(alarm["raw"])
        if match:
            self.open_panel_requested.emit(
                "Tráfego (gráfico)",
                [parsers.expand_iface_name(match.group(0))])

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if self.follow_chk.isChecked() and not self._timer.isActive():
            self._follow_toggled()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self.session.scheduler.cancel("alarms")
        super().closeEvent(event)


_UNSUPPORTED_RE = re.compile(
    r"^\s*\^|Error:\s*Unrecognized|Wrong parameter|Unrecognized command",
    re.MULTILINE)


def _looks_unsupported(text: str) -> bool:
    """Resposta de comando inexistente (o VRP responde com '^' e 'Error')."""
    return bool(_UNSUPPORTED_RE.search(text))
