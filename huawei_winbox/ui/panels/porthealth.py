"""Painel Saúde das portas — diagnóstico óptico e erros por interface.

Duas perguntas que um provedor faz todo dia e que nenhum painel respondia
direito:

**"Qual fibra está prestes a cair?"**
    A aba *Óptica* lista o diagnóstico (DOM) de todos os transceptores com
    os **limiares do próprio módulo**. Um número solto não diz nada — −24
    dBm é ruim num módulo cujo piso é −14 e normal num de −28 — então o
    que importa é a **folga**: quantos dB ainda faltam para o piso. Abaixo
    de 3 dB a porta entra em atenção antes de qualquer alarme disparar.

**"Que porta está com erro AGORA?"**
    A aba *Erros* mostra quanto os contadores subiram na janela escolhida,
    não o acumulado desde o boot. Cinco milhões de erros de três anos
    atrás é ruído; duzentos no último minuto é incêndio. Os contadores
    saem do mesmo ``display interface brief`` que a telemetria já executa,
    então acompanhar isso não custa consulta nenhuma a mais.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QCheckBox, QPlainTextEdit, QSplitter, QMessageBox, QFileDialog,
)

from ...core import parsers, telemetry
from ...core import history as history_mod
from ...core.scheduler import BACKGROUND, NORMAL, Dropped
from ..widgets.traffic_graph import MiniChart
from ..worker import run_async

_TIMEOUT = 60.0
_REFRESH_SECONDS = [30, 60, 300]
_WINDOWS = [("1 min", 60), ("5 min", 300), ("15 min", 900),
            ("1 hora", 3600), ("Tudo", None)]

_STATUS_BG = {
    "crítico": QColor("#f8d7da"),
    "atenção": QColor("#fff4e0"),
}
_STATUS_FG = {
    "crítico": QColor("#96281b"),
    "atenção": QColor("#8a6100"),
    "ok": QColor("#1e7a45"),
    "sem limiar": QColor("#7a7a7a"),
}
# Quantas portas consultar por lote ao descobrir limiares.
_THRESHOLD_BATCH = 6

# Comandos tentados em ordem: o nome do diagnóstico muda por plataforma.
_DOM_COMMANDS = [
    "display transceiver diagnosis interface",
    "display transceiver all",
    "display transceiver verbose",
]


class PortHealthPanel(QWidget):
    title = "Saúde das portas"
    state_key = "porthealth"

    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history = history_mod.for_session(session)
        self.engine = telemetry.for_session(session, self.history)
        self._busy = False
        self._optics: dict[str, dict] = {}
        self._raw = ""
        # Limiares são propriedade do módulo, não medição: lidos uma vez por
        # porta e guardados. A forma de tabela do diagnóstico não os traz, e
        # sem eles não dá para dizer se −13 dBm é bom ou ruim.
        self._thresholds: dict[str, dict] = {}
        self._threshold_misses: set[str] = set()
        self._learning = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_toolbar())

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_optics_tab(), "Óptica")
        self._tabs.addTab(self._build_errors_tab(), "Erros")
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
        self._timer.timeout.connect(self._auto_tick)
        self._view_timer = QTimer(self)
        self._view_timer.timeout.connect(self._refresh_errors)
        self._view_timer.start(2000)

        self.engine.acquire_traffic("porthealth")
        self.engine.start()
        self.refresh()

    # ================================================================== #
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)

        self.auto_chk = QCheckBox("Auto")
        self.auto_chk.setToolTip(
            "Relê o diagnóstico óptico periodicamente, em segundo plano.")
        self.auto_chk.toggled.connect(self._auto_toggled)
        bar.addWidget(self.auto_chk)
        self.auto_combo = QComboBox()
        for seconds in _REFRESH_SECONDS:
            self.auto_combo.addItem(f"{seconds}s", seconds)
        self.auto_combo.setCurrentIndex(1)
        self.auto_combo.currentIndexChanged.connect(self._auto_toggled)
        bar.addWidget(self.auto_combo)

        bar.addWidget(QLabel("Janela dos erros:"))
        self.window_combo = QComboBox()
        for label, seconds in _WINDOWS:
            self.window_combo.addItem(label, seconds)
        self.window_combo.setCurrentIndex(1)
        self.window_combo.currentIndexChanged.connect(self._refresh_errors)
        bar.addWidget(self.window_combo)

        self.problems_chk = QCheckBox("Só o que preocupa")
        self.problems_chk.setToolTip(
            "Esconde as portas em situação normal e sem erros novos.")
        self.problems_chk.toggled.connect(self._apply_filters)
        bar.addWidget(self.problems_chk)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar portas…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filters)
        bar.addWidget(self.filter_edit, 1)

        export_btn = QPushButton("CSV…")
        export_btn.clicked.connect(self._export)
        bar.addWidget(export_btn)
        return bar

    def _build_optics_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        splitter = QSplitter(Qt.Vertical)
        self.optics = QTableWidget(0, 9)
        self.optics.setHorizontalHeaderLabels(
            ["Porta", "Situação", "RX (dBm)", "Folga", "TX (dBm)",
             "Temp. (°C)", "Corrente (mA)", "Tipo", "Observação"])
        _prepare(self.optics, stretch=8)
        self.optics.itemSelectionChanged.connect(self._optic_selected)
        self.optics.itemDoubleClicked.connect(self._jump_to_graph)
        splitter.addWidget(self.optics)

        self.rx_chart = MiniChart("Potência recebida (RX)", " dBm",
                                  color="#7d3c98", maximum=None)
        self.rx_chart.setMinimumHeight(90)
        splitter.addWidget(self.rx_chart)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return page

    def _build_errors_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        self.errors = QTableWidget(0, 6)
        self.errors.setHorizontalHeaderLabels(
            ["Porta", "Estado", "Novos (entrada)", "Novos (saída)",
             "Total entrada", "Total saída"])
        _prepare(self.errors, stretch=5)
        self.errors.itemDoubleClicked.connect(self._jump_to_graph)
        layout.addWidget(self.errors)
        self.errors_note = QLabel("")
        self.errors_note.setWordWrap(True)
        layout.addWidget(self.errors_note)
        return page

    # ================================================================== #
    # Diagnóstico óptico
    # ================================================================== #
    def refresh(self, *_args, background: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)
        if not background:
            self.status.setText("Lendo o diagnóstico óptico…")
        session = self.session
        interval = self.auto_combo.currentData() or 60
        priority = BACKGROUND if background else NORMAL

        def fetch() -> dict:
            for command in _DOM_COMMANDS:
                try:
                    text = session.run(
                        command, timeout=_TIMEOUT, priority=priority, ttl=10.0,
                        max_wait=interval * 2 if background else None,
                        tag="porthealth")
                except Dropped:
                    return {"text": "", "command": ""}
                except Exception:            # noqa: BLE001 — comando ausente
                    continue
                if text.strip() and parsers.parse_transceiver_diagnosis(text):
                    return {"text": text, "command": command}
            return {"text": "", "command": ""}

        def done(data: dict) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            if not data["command"]:
                self.status.setText(
                    "Este equipamento não respondeu a nenhum comando de "
                    "diagnóstico óptico (as portas podem não ter módulo DOM).")
                self._optics = {}
                self._fill_optics()
                return
            self._raw = f"> {data['command']}\n{data['text']}"
            self.output.setPlainText(self._raw)
            self._optics = parsers.parse_transceiver_diagnosis(data["text"])
            now = time.time()
            for name, entry in self._optics.items():
                if entry["rx_power"] is not None:
                    self.history.add_metric(f"rx:{name}", entry["rx_power"],
                                            when=now)
            self._apply_thresholds()
            self._fill_optics()
            self.status.setText(f"lido às {time.strftime('%H:%M:%S')}")
            self._learn_thresholds()

        def fail(message: str) -> None:
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status.setText(f"⚠ {message.splitlines()[0][:110]}")

        run_async(fetch, done, fail)

    # -- descoberta dos limiares --------------------------------------- #
    def _apply_thresholds(self) -> None:
        """Aplica os limiares já conhecidos e reavalia cada porta."""
        for name, entry in self._optics.items():
            known = self._thresholds.get(name)
            if not known:
                continue
            entry.update(known)
            parsers.evaluate_dom(entry)

    def _learn_thresholds(self) -> None:
        """Busca os limiares das portas que ainda não os têm, aos poucos.

        Um comando por porta seria caro numa placa cheia, então vai em
        lotes pequenos, com prioridade de fundo e prazo curto: se o
        equipamento estiver ocupado, o pedido é descartado e tenta-se no
        próximo ciclo. Cada porta é consultada **uma vez** — limiar não
        muda enquanto o módulo for o mesmo.
        """
        if self._learning:
            return
        pending = [name for name, entry in self._optics.items()
                   if entry["rx_low"] is None
                   and name not in self._thresholds
                   and name not in self._threshold_misses
                   and parsers.is_physical_iface(name)]
        if not pending:
            return
        batch = pending[:_THRESHOLD_BATCH]
        self._learning = True
        session = self.session

        def fetch() -> dict:
            found = {}
            for name in batch:
                argument = parsers.transceiver_iface_arg(name)
                try:
                    text = session.run(
                        f"display transceiver interface {argument} verbose",
                        timeout=20.0, priority=BACKGROUND, ttl=3600.0,
                        max_wait=90.0, tag="porthealth")
                except Exception:            # noqa: BLE001 — segue o baile
                    continue
                parsed = parsers.parse_transceiver_diagnosis(
                    text, default_name=name)
                if not parsed:
                    continue
                entry = parsed.get(name) or next(iter(parsed.values()))
                values = {key: entry[key] for key in parsers.DOM_THRESHOLD_KEYS
                          if entry[key] is not None}
                # A forma de tabela não traz tipo nem fabricante do módulo;
                # já que a consulta foi feita, aproveita os dois.
                for extra in ("type", "vendor", "wavelength"):
                    if entry.get(extra):
                        values[extra] = entry[extra]
                if values:
                    found[name] = values
            return found

        def done(found: dict) -> None:
            self._learning = False
            for name in batch:
                if name in found:
                    self._thresholds[name] = found[name]
                else:
                    # Módulo sem DOM ou comando recusado: não insiste.
                    self._threshold_misses.add(name)
            if found:
                self._apply_thresholds()
                self._fill_optics()
            remaining = sum(
                1 for name, entry in self._optics.items()
                if entry["rx_low"] is None and name not in self._thresholds
                and name not in self._threshold_misses)
            if remaining and self.isVisible():
                QTimer.singleShot(700, self._learn_thresholds)

        def fail(_message: str) -> None:
            self._learning = False

        run_async(fetch, done, fail)

    def _auto_toggled(self) -> None:
        if self.auto_chk.isChecked():
            self._timer.start((self.auto_combo.currentData() or 60) * 1000)
        else:
            self._timer.stop()

    def _auto_tick(self) -> None:
        if self.isVisible():
            self.refresh(background=True)

    def _fill_optics(self) -> None:
        rows = sorted(self._optics.values(),
                      key=lambda e: (_severity_rank(e["status"]), e["name"]))
        self.optics.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            margin = (f"{entry['margin_db']:+.1f} dB"
                      if entry["margin_db"] is not None else "—")
            values = [
                entry["name"], entry["status"] or "—",
                _fmt(entry["rx_power"]), margin, _fmt(entry["tx_power"]),
                _fmt(entry["temperature"], 0), _fmt(entry["bias"]),
                entry["type"] or "—", entry.get("detail", ""),
            ]
            for column, text in enumerate(values):
                item = self.optics.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.optics.setItem(row, column, item)
                item.setText(str(text))
                item.setData(Qt.UserRole, entry)
                background = _STATUS_BG.get(entry["status"])
                item.setBackground(background if background else Qt.transparent)
                foreground = _STATUS_FG.get(entry["status"])
                item.setForeground(foreground if foreground
                                   else QColor("#1c1c1c"))
        self._apply_filters()
        self._update_summary()

    def _optic_selected(self) -> None:
        items = self.optics.selectedItems()
        if not items:
            return
        entry = items[0].data(Qt.UserRole)
        if not entry:
            return
        name = entry["name"]
        samples = self.history.metric_samples(f"rx:{name}",
                                              time.time() - 3600, 400)
        self.rx_chart.set_samples(
            samples, _fmt(entry["rx_power"]) + " dBm"
            if entry["rx_power"] is not None else "—")
        self.rx_chart._label = f"Potência recebida — {name}"

    # ================================================================== #
    # Erros
    # ================================================================== #
    def _refresh_errors(self) -> None:
        if not self.isVisible():
            return
        seconds = self.window_combo.currentData()
        since = None if seconds is None else time.time() - seconds
        snapshot = self.engine.snapshot()
        totals = snapshot.get("errors") or {}
        meta = self.engine.interface_meta()

        rows = []
        for name, (in_total, out_total) in totals.items():
            in_new = _delta(self.history, f"errin:{name}", since)
            out_new = _delta(self.history, f"errout:{name}", since)
            rows.append({
                "name": name,
                "state": (meta.get(name) or {}).get("oper", ""),
                "in_new": in_new, "out_new": out_new,
                "in_total": in_total, "out_total": out_total,
            })
        rows.sort(key=lambda r: (-(r["in_new"] + r["out_new"]),
                                 -(r["in_total"] + r["out_total"]), r["name"]))

        self.errors.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            fresh = entry["in_new"] + entry["out_new"]
            values = [entry["name"], entry["state"] or "—",
                      f"+{entry['in_new']}" if entry["in_new"] else "0",
                      f"+{entry['out_new']}" if entry["out_new"] else "0",
                      str(entry["in_total"]), str(entry["out_total"])]
            for column, text in enumerate(values):
                item = self.errors.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.errors.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.UserRole, entry)
                if fresh:
                    item.setBackground(QColor("#fdecea"))
                    item.setForeground(QColor("#c0392b"))
                else:
                    item.setBackground(Qt.transparent)
                    item.setForeground(QColor("#1c1c1c"))
        if not totals:
            self.errors_note.setText(
                "O equipamento não expôs contadores de erro no "
                "'display interface brief'. Com SNMP habilitado eles vêm "
                "da IF-MIB — configure a comunidade em Configurações.")
        else:
            window = self.window_combo.currentText()
            self.errors_note.setText(
                f"'Novos' é quanto o contador subiu nos últimos {window} — "
                f"é esse número que aponta o problema de agora. O total "
                f"acumula desde o último boot do equipamento.")
        self._apply_filters()
        self._update_summary()

    # ================================================================== #
    def _apply_filters(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        only_problems = self.problems_chk.isChecked()

        for row in range(self.optics.rowCount()):
            item = self.optics.item(row, 0)
            entry = item.data(Qt.UserRole) if item else None
            visible = bool(entry) and (not needle
                                       or needle in entry["name"].lower())
            if visible and only_problems:
                visible = entry["status"] in ("crítico", "atenção")
            self.optics.setRowHidden(row, not visible)

        for row in range(self.errors.rowCount()):
            item = self.errors.item(row, 0)
            entry = item.data(Qt.UserRole) if item else None
            visible = bool(entry) and (not needle
                                       or needle in entry["name"].lower())
            if visible and only_problems:
                visible = (entry["in_new"] + entry["out_new"]) > 0
            self.errors.setRowHidden(row, not visible)

    def _update_summary(self) -> None:
        criticos = sum(1 for e in self._optics.values()
                       if e["status"] == "crítico")
        atencao = sum(1 for e in self._optics.values()
                      if e["status"] == "atenção")
        com_erro = sum(1 for row in range(self.errors.rowCount())
                       if (self.errors.item(row, 0)
                           and self.errors.item(row, 0).data(Qt.UserRole)
                           and (self.errors.item(row, 0).data(Qt.UserRole)["in_new"]
                                + self.errors.item(row, 0).data(Qt.UserRole)["out_new"]) > 0))
        sem_limiar = sum(1 for e in self._optics.values()
                         if e["status"] == "sem limiar")
        parts = [f"{len(self._optics)} porta(s) com óptica"]
        if criticos:
            parts.append(f"{criticos} crítica(s)")
        if atencao:
            parts.append(f"{atencao} em atenção")
        if com_erro:
            parts.append(f"{com_erro} com erro novo")
        if sem_limiar:
            parts.append(f"{sem_limiar} aguardando limiares")
        if not criticos and not atencao and not com_erro and self._optics:
            parts.append("nada preocupante")
        self.summary.setText("  ·  ".join(parts))

    def _jump_to_graph(self, item) -> None:
        entry = item.data(Qt.UserRole)
        if entry and entry.get("name"):
            self.open_panel_requested.emit("Tráfego (gráfico)",
                                           [entry["name"]])

    def _export(self) -> None:
        if not self._optics and not self.errors.rowCount():
            QMessageBox.information(self, self.title, "Nada para exportar.")
            return
        stamp = time.strftime("%Y%m%d-%H%M")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Exportar saúde das portas",
            f"saude-portas-{self.session.hostname}-{stamp}.csv",
            "CSV (*.csv)")
        if not path:
            return
        import csv
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["porta", "situacao", "rx_dbm", "folga_db",
                                 "tx_dbm", "temperatura_c", "corrente_ma",
                                 "tipo", "observacao"])
                for entry in sorted(self._optics.values(),
                                    key=lambda e: e["name"]):
                    writer.writerow([
                        entry["name"], entry["status"], entry["rx_power"],
                        entry["margin_db"], entry["tx_power"],
                        entry["temperature"], entry["bias"], entry["type"],
                        entry.get("detail", "")])
                writer.writerow([])
                writer.writerow(["porta", "estado", "erros_novos_entrada",
                                 "erros_novos_saida", "total_entrada",
                                 "total_saida"])
                for row in range(self.errors.rowCount()):
                    item = self.errors.item(row, 0)
                    entry = item.data(Qt.UserRole) if item else None
                    if entry:
                        writer.writerow([
                            entry["name"], entry["state"], entry["in_new"],
                            entry["out_new"], entry["in_total"],
                            entry["out_total"]])
        except OSError as exc:
            QMessageBox.warning(self, self.title, f"Falha ao gravar: {exc}")
            return
        self.status.setText(f"Exportado para {path}")

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._timer.stop()
        self._view_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if not self._view_timer.isActive():
            self._view_timer.start(2000)
        if self.auto_chk.isChecked() and not self._timer.isActive():
            self._auto_toggled()
        super().showEvent(event)
        self._refresh_errors()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._view_timer.stop()
        self.engine.release_traffic("porthealth")
        self.session.scheduler.cancel("porthealth")
        super().closeEvent(event)


# ── auxiliares ───────────────────────────────────────────────────────── #
def _prepare(table: QTableWidget, stretch: int) -> None:
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    for column in range(table.columnCount()):
        header.setSectionResizeMode(
            column, QHeaderView.Stretch if column == stretch
            else QHeaderView.ResizeToContents)


def _fmt(value, decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def _severity_rank(status: str) -> int:
    return {"crítico": 0, "atenção": 1, "sem limiar": 2, "ok": 3}.get(status, 4)


def _delta(history, metric: str, since: float | None) -> int:
    """Quanto o contador subiu na janela (0 se reiniciou ou não há dado)."""
    samples = history.metric_samples(metric, since)
    if len(samples) < 2:
        return 0
    first, last = samples[0][1], samples[-1][1]
    # Contador reiniciado (reboot da placa): a diferença negativa não
    # significa "melhorou", significa "recomeçou" — vale zero.
    return int(last - first) if last >= first else 0
