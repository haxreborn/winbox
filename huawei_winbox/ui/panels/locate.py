"""Painel Localizar — em que porta está este cliente?

Digite o IP, o MAC (em qualquer notação) ou um pedaço da descrição da
porta, e o aplicativo encadeia as consultas: o ARP dá o MAC, a tabela MAC
dá a porta física, o BRAS dá a sessão PPPoE e a rota diz por onde o
tráfego sai. O que era três ou quatro comandos e um cruzamento mental
vira uma resposta só.

Como em todo o resto do aplicativo, os comandos executados ficam à vista —
nada acontece por trás.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit, QGroupBox,
    QMessageBox, QComboBox, QApplication,
)

from ...core import locate as locate_core
from ...core import parsers
from ...core.scheduler import INTERACTIVE
from ..worker import run_async

_SOURCE_COLOR = {
    "Tabela MAC": QColor("#1e7a45"),
    "PPPoE / BAS": QColor("#7d3c98"),
    "ARP": QColor("#1f6fb2"),
    "Descrição da porta": QColor("#8a6100"),
    "Rota": QColor("#5a5a5a"),
}
_HISTORY_LIMIT = 12


class LocatePanel(QWidget):
    title = "Localizar"
    state_key = "locate"

    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._result: dict | None = None
        self._descriptions: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        # ── Busca ── #
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(QLabel("Procurar:"))
        self.query = QComboBox()
        self.query.setEditable(True)
        self.query.setInsertPolicy(QComboBox.NoInsert)
        self.query.setMinimumWidth(280)
        self.query.lineEdit().setPlaceholderText(
            "IP, MAC (qualquer formato) ou parte da descrição da porta")
        self.query.lineEdit().returnPressed.connect(self._search)
        bar.addWidget(self.query, 1)
        self.search_btn = QPushButton("Localizar")
        self.search_btn.clicked.connect(self._search)
        bar.addWidget(self.search_btn)
        root.addLayout(bar)

        # ── Resposta em uma frase ── #
        self.summary = QLabel(
            "Informe um IP, um MAC ou um trecho da descrição e clique em "
            "Localizar.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        font = QFont(self.font())
        font.setBold(True)
        self.summary.setFont(font)
        root.addWidget(self.summary)

        # ── Achados ── #
        box = QGroupBox("Onde foi encontrado")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(6, 4, 6, 6)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Fonte", "Interface", "Detalhe"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(self._open_graph_from_item)
        box_layout.addWidget(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        for text, slot, tip in [
            ("Ver no gráfico", self._open_graph,
             "Abre o Tráfego com a porta encontrada marcada."),
            ("Abrir Interfaces", self._open_interfaces,
             "Abre a lista de interfaces filtrada nessa porta."),
            ("Copiar resumo", self._copy,
             "Copia a resposta para colar num chamado."),
        ]:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        self.status = QLabel("")
        actions.addWidget(self.status)
        box_layout.addLayout(actions)
        root.addWidget(box, 1)

        # ── Comandos executados ── #
        commands_box = QGroupBox("Comandos executados no equipamento")
        commands_layout = QVBoxLayout(commands_box)
        commands_layout.setContentsMargins(6, 4, 6, 6)
        self.commands = QPlainTextEdit()
        self.commands.setObjectName("terminal")
        self.commands.setReadOnly(True)
        self.commands.setMaximumHeight(96)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.commands.setFont(mono)
        commands_layout.addWidget(self.commands)
        root.addWidget(commands_box)

    # ================================================================== #
    def focus_payload(self, payload) -> None:
        """Busca pedida por outro painel."""
        text = payload[0] if isinstance(payload, (list, tuple)) else payload
        if text:
            self.query.setCurrentText(str(text))
            self._search()

    def collect_state(self) -> dict:
        return {"history": [self.query.itemText(i)
                            for i in range(self.query.count())][:_HISTORY_LIMIT]}

    # ================================================================== #
    def _search(self) -> None:
        query = self.query.currentText().strip()
        if not query:
            return
        if self._busy:
            return
        self._remember(query)
        self._busy = True
        self.search_btn.setEnabled(False)
        self.status.setText("procurando…")
        self.summary.setText(f"Procurando “{query}”…")
        session = self.session
        descriptions = dict(self._descriptions)

        def run(command: str, timeout: float = 30.0) -> str:
            return session.run(command, timeout=timeout, priority=INTERACTIVE,
                               ttl=3.0, tag="locate")

        def fetch() -> dict:
            known = descriptions
            kind, _value = locate_core.classify(query)
            if kind == locate_core.KIND_TEXT and not known:
                # A descrição das portas só é baixada quando a busca é por
                # texto — nas buscas por IP ou MAC ela não faria diferença.
                try:
                    rows = parsers.parse_interface_description(
                        session.run("display interface description",
                                    timeout=40.0, priority=INTERACTIVE,
                                    ttl=60.0, tag="locate"))
                    known = {row["name"]: row["description"] for row in rows}
                except Exception:            # noqa: BLE001 — busca não quebra
                    known = {}
            result = locate_core.search(run, query, known)
            result["_descriptions"] = known
            return result

        def done(result: dict) -> None:
            self._busy = False
            self.search_btn.setEnabled(True)
            self.status.setText("")
            if result.get("_descriptions"):
                self._descriptions = result["_descriptions"]
            self._result = result
            self._fill(result)

        def fail(message: str) -> None:
            self._busy = False
            self.search_btn.setEnabled(True)
            self.status.setText("")
            self.summary.setText(f"Falha na busca: {message.splitlines()[0]}")

        run_async(fetch, done, fail)

    def _remember(self, query: str) -> None:
        existing = [self.query.itemText(i) for i in range(self.query.count())]
        if query in existing:
            return
        self.query.insertItem(0, query)
        while self.query.count() > _HISTORY_LIMIT:
            self.query.removeItem(self.query.count() - 1)
        self.query.setCurrentText(query)

    def _fill(self, result: dict) -> None:
        self.summary.setText(locate_core.describe(result))
        findings = result["findings"]
        self.table.setRowCount(len(findings))
        for row, finding in enumerate(findings):
            values = [finding.source, finding.interface or "—",
                      finding.summary]
            for column, text in enumerate(values):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.UserRole, finding.interface)
            color = _SOURCE_COLOR.get(finding.source)
            if color is not None:
                self.table.item(row, 0).setForeground(color)
        if findings:
            self.table.selectRow(0)
        self.commands.setPlainText(
            "\n".join(result["steps"])
            + f"\n\n({len(result['steps'])} comando(s) às "
              f"{time.strftime('%H:%M:%S')})")

    # ================================================================== #
    def _best_interface(self) -> str:
        items = self.table.selectedItems()
        if items:
            name = items[0].data(Qt.UserRole)
            if name and name != "—":
                return name
        if self._result and self._result["interfaces"]:
            return self._result["interfaces"][0]
        return ""

    def _open_graph(self) -> None:
        name = self._best_interface()
        if not name:
            QMessageBox.information(
                self, self.title, "Nenhuma porta foi encontrada ainda.")
            return
        self.open_panel_requested.emit("Tráfego (gráfico)", [name])

    def _open_graph_from_item(self, item) -> None:
        name = item.data(Qt.UserRole)
        if name and name != "—":
            self.open_panel_requested.emit("Tráfego (gráfico)", [name])

    def _open_interfaces(self) -> None:
        name = self._best_interface()
        if not name:
            QMessageBox.information(
                self, self.title, "Nenhuma porta foi encontrada ainda.")
            return
        self.open_panel_requested.emit("Interfaces", [name])

    def _copy(self) -> None:
        if not self._result:
            return
        lines = [f"Busca: {self._result['query']}",
                 locate_core.describe(self._result), ""]
        for finding in self._result["findings"]:
            lines.append(f"- [{finding.source}] {finding.interface or '—'}: "
                         f"{finding.summary}")
        lines += ["", "Comandos:"] + [f"  {c}" for c in self._result["steps"]]
        QApplication.clipboard().setText("\n".join(lines))
        self.status.setText("resumo copiado")

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        self.session.scheduler.cancel("locate")
        super().closeEvent(event)
