"""Painel Verificação — este equipamento está bem?

Junta o que os outros painéis já sabem numa resposta única, do tipo que se
quer ao assumir um plantão ou ao terminar uma manutenção. Cada linha diz o
que foi conferido, o veredito, o que foi observado e o que fazer — e leva
ao painel onde o assunto se resolve.

O resultado pode ser copiado como texto, para colar num chamado ou numa
passagem de turno.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QApplication, QCheckBox,
)

from ...core import healthcheck, parsers, telemetry
from ...core import history as history_mod
from ...core.scheduler import NORMAL
from ..worker import run_async

_STATUS_STYLE = {
    healthcheck.FAIL: (QColor("#f8d7da"), QColor("#96281b")),
    healthcheck.WARN: (QColor("#fff4e0"), QColor("#8a6100")),
    healthcheck.OK: (None, QColor("#1e7a45")),
    healthcheck.UNKNOWN: (None, QColor("#7a8797")),
}
_VERDICT_TEXT = {
    healthcheck.FAIL: "Precisa de atenção agora",
    healthcheck.WARN: "Funcionando, com ressalvas",
    healthcheck.OK: "Sem problemas aparentes",
    healthcheck.UNKNOWN: "Não foi possível avaliar",
}
# Janela usada para decidir se um contador de erro "está subindo".
_ERROR_WINDOW = 300.0


class HealthCheckPanel(QWidget):
    title = "Verificação"
    state_key = "healthcheck"

    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history = history_mod.for_session(session)
        self.engine = telemetry.for_session(session, self.history)
        self._busy = False
        self._results: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.run_btn = QPushButton("Verificar agora")
        self.run_btn.clicked.connect(self.refresh)
        bar.addWidget(self.run_btn)
        self.config_chk = QCheckBox("comparar com a configuração salva")
        self.config_chk.setChecked(True)
        self.config_chk.setToolTip(
            "Baixa a configuração em execução e a salva na flash para "
            "descobrir o que se perderia num reboot. É a conferência mais "
            "cara da lista — desmarque se o equipamento estiver lento.")
        bar.addWidget(self.config_chk)
        copy_btn = QPushButton("Copiar resultado")
        copy_btn.clicked.connect(self._copy)
        bar.addWidget(copy_btn)
        bar.addStretch(1)
        self.status = QLabel("")
        bar.addWidget(self.status)
        root.addLayout(bar)

        self.verdict = QLabel("Clique em “Verificar agora”.")
        self.verdict.setWordWrap(True)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(self.font().pointSizeF() + 1.5)
        self.verdict.setFont(font)
        root.addWidget(self.verdict)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Veredito", "Conferência", "Observado", "O que fazer"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(self._open_related)
        root.addWidget(self.table, 1)

        hint = QLabel("Duplo clique numa linha abre o painel onde o assunto "
                      "se resolve.")
        hint.setObjectName("itemCount")
        root.addWidget(hint)

        self.refresh()

    # ================================================================== #
    def refresh(self, *_args) -> None:
        if self._busy:
            return
        self._busy = True
        self.run_btn.setEnabled(False)
        self.status.setText("conferindo…")
        session = self.session
        engine = self.engine
        history = self.history
        compare_config = self.config_chk.isChecked()
        snapshot = engine.snapshot()

        def query(command: str, timeout: float) -> str:
            try:
                return session.run(command, timeout=timeout, priority=NORMAL,
                                   ttl=15.0, tag="healthcheck")
            except Exception:              # noqa: BLE001 — vira "n/d"
                return ""

        def collect() -> dict:
            data: dict = {"health": snapshot["health"]}

            version = parsers.parse_version(query("display version", 20.0))
            data["uptime_hours"] = healthcheck.parse_uptime_hours(
                version.get("uptime", ""))

            alarm_text = query("display alarm active", 30.0)
            if alarm_text.strip():
                data["alarms"] = parsers.parse_active_alarms(alarm_text)

            interfaces = parsers.parse_interface_description(
                query("display interface description", 40.0))
            if interfaces:
                data["interfaces"] = interfaces

            optics = {}
            for command in ("display transceiver diagnosis interface",
                            "display transceiver all"):
                optics = parsers.parse_transceiver_diagnosis(
                    query(command, 60.0))
                if optics:
                    break
            learned = engine.optical_thresholds()
            for name, entry in optics.items():
                values = learned.get(name)
                if values:
                    entry.update(values)
                    parsers.evaluate_dom(entry)
            if optics:
                data["optics"] = optics

            # Erros crescendo: sai do histórico que a telemetria alimenta,
            # sem consultar nada.
            totals = snapshot.get("errors") or {}
            if totals:
                since = time.time() - _ERROR_WINDOW
                growing = []
                for name in totals:
                    delta = (_delta(history, f"errin:{name}", since)
                             + _delta(history, f"errout:{name}", since))
                    if delta > 0:
                        growing.append((name, delta))
                growing.sort(key=lambda item: -item[1])
                data["growing_errors"] = growing

            peers = parsers.parse_bgp_peers(query("display bgp peer", 30.0))
            if peers:
                data["bgp"] = peers
            ospf = parsers.parse_ospf_peers(
                query("display ospf peer brief", 25.0))
            if ospf:
                data["ospf"] = ospf

            ntp = parsers.parse_ntp_status(query("display ntp status", 20.0))
            if ntp and ntp != "—":
                data["ntp"] = ntp

            communities = parsers.parse_snmp_communities(
                query("display snmp-agent community", 20.0))
            if communities:
                data["snmp_communities"] = communities
            telnet = query("display telnet server status", 15.0)
            if telnet.strip():
                data["telnet_enabled"] = "enable" in telnet.lower()

            if compare_config:
                current = query("display current-configuration", 120.0)
                saved = query("display saved-configuration", 120.0)
                if current.strip() and saved.strip():
                    from .config import normalize_for_diff
                    import difflib
                    diff = list(difflib.unified_diff(
                        normalize_for_diff(saved), normalize_for_diff(current),
                        lineterm="", n=0))
                    data["unsaved_lines"] = sum(
                        1 for line in diff if line[:1] in "+-"
                        and not line.startswith(("+++", "---")))
            return data

        def done(data: dict) -> None:
            self._busy = False
            self.run_btn.setEnabled(True)
            self.status.setText(f"conferido às {time.strftime('%H:%M:%S')}")
            self._results = healthcheck.run(data)
            self._fill()

        def fail(message: str) -> None:
            self._busy = False
            self.run_btn.setEnabled(True)
            self.status.setText("")
            self.verdict.setText(f"Falha ao conferir: "
                                 f"{message.splitlines()[0][:120]}")

        run_async(collect, done, fail)

    def _fill(self) -> None:
        verdict, phrase = healthcheck.summarize(self._results)
        _background, foreground = _STATUS_STYLE[verdict]
        self.verdict.setText(f"{_VERDICT_TEXT[verdict]} — {phrase}")
        self.verdict.setStyleSheet(f"color:{foreground.name()}")

        self.table.setRowCount(len(self._results))
        for row, check in enumerate(self._results):
            values = [check.status, check.title, check.detail, check.hint]
            background, foreground = _STATUS_STYLE.get(
                check.status, (None, QColor("#1c1c1c")))
            for column, text in enumerate(values):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.UserRole, check.panel)
                item.setBackground(background if background
                                   else Qt.transparent)
                if column == 0:
                    item.setForeground(foreground)
                    font = item.font()
                    font.setBold(check.status in (healthcheck.FAIL,
                                                  healthcheck.WARN))
                    item.setFont(font)
        self.table.resizeRowsToContents()

    def _open_related(self, item) -> None:
        panel = item.data(Qt.UserRole)
        if panel:
            self.open_panel_requested.emit(panel, None)

    def _copy(self) -> None:
        if not self._results:
            return
        verdict, phrase = healthcheck.summarize(self._results)
        lines = [f"Verificação de {self.session.hostname} "
                 f"({self.session.host}) — {time.strftime('%d/%m/%Y %H:%M')}",
                 f"{_VERDICT_TEXT[verdict]} — {phrase}", ""]
        for check in self._results:
            lines.append(f"[{check.status.upper():>7}] {check.title}: "
                         f"{check.detail}")
            if check.hint and check.status in (healthcheck.FAIL,
                                               healthcheck.WARN):
                lines.append(f"          → {check.hint}")
        QApplication.clipboard().setText("\n".join(lines))
        self.status.setText("resultado copiado")

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        self.session.scheduler.cancel("healthcheck")
        super().closeEvent(event)


def _delta(history, metric: str, since: float) -> int:
    samples = history.metric_samples(metric, since)
    if len(samples) < 2:
        return 0
    first, last = samples[0][1], samples[-1][1]
    return int(last - first) if last >= first else 0
