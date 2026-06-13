"""Painel Hardware — CPU, memória, temperatura, fans, energia e transceivers."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QGridLayout,
)
from PySide6.QtCore import Qt

from ...core import parsers
from ..worker import run_async


def _clean_or_na(text: str) -> str:
    """Retorna o texto limpo, ou mensagem amigável se for erro VRP."""
    if not text or not text.strip():
        return ""
    if re.search(r"^\s*\^|Error:|Unrecognized command", text, re.MULTILINE):
        return None   # None indica 'não suportado'
    return text


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

        self._tabs = QTabWidget()

        # ── CPU / Memória ── #
        cpu_w = QWidget()
        cpu_vbox = QVBoxLayout(cpu_w)
        self._cpu_grp = QGroupBox("Utilização atual")
        self._cpu_grid = QGridLayout(self._cpu_grp)
        cpu_vbox.addWidget(self._cpu_grp)
        self._cpu_raw = self._make_raw()
        cpu_vbox.addWidget(QLabel("<b>Detalhes:</b>"))
        cpu_vbox.addWidget(self._cpu_raw)
        self._tabs.addTab(cpu_w, "CPU / Memória")

        # ── Chassi / Fans / Energia ── #
        self._hw_raw = self._make_raw()
        self._tabs.addTab(self._hw_raw, "Chassi / Fans / Energia")

        # ── Temperatura ── #
        env_w = QWidget()
        env_vbox = QVBoxLayout(env_w)
        self._env_table = self._make_table(
            ["Componente", "Temperatura", "Limite Superior", "Limite Inferior"])
        env_vbox.addWidget(self._env_table)
        self._env_notice = QLabel("")
        env_vbox.addWidget(self._env_notice)
        self._tabs.addTab(env_w, "Temperatura")

        # ── Transceivers / Óptica ── #
        opt_w = QWidget()
        opt_vbox = QVBoxLayout(opt_w)
        self._opt_table = self._make_table(
            ["Interface", "Tipo", "TX Power (dBm)", "RX Power (dBm)",
             "Temperatura (°C)", "Corrente (mA)"])
        opt_vbox.addWidget(self._opt_table)
        self._opt_notice = QLabel("")
        opt_vbox.addWidget(self._opt_notice)
        self._tabs.addTab(opt_w, "Transceivers (óptica)")

        root.addWidget(self._tabs)
        self.refresh()

    # ── helpers ── #

    @staticmethod
    def _make_raw() -> QPlainTextEdit:
        w = QPlainTextEdit()
        w.setReadOnly(True)
        w.setObjectName("terminal")
        return w

    @staticmethod
    def _make_table(cols: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _set_table_rows(self, table: QTableWidget,
                        rows: list[list[str]]) -> None:
        table.setRowCount(0)
        for row_data in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, c, item)

    # ── fetch ── #

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)
        self.status_lbl.setText("Consultando equipamento…")

        def fetch():
            results = {}
            for key, cmd, timeout in [
                ("cpu",     "display cpu-usage",        15.0),
                ("mem",     "display memory-usage",     15.0),
                ("device",  "display device",           30.0),
                ("env",     "display environment",      20.0),
                ("optical", "display transceiver all",  30.0),
            ]:
                try:
                    results[key] = self.session.run(cmd, timeout=timeout)
                except Exception as exc:
                    results[key] = f"[Erro: {exc}]"
            return results

        def done(results: dict):
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status_lbl.setText("Atualizado")
            self._populate(results)

        def fail(msg: str):
            self._busy = False
            self.refresh_btn.setEnabled(True)
            self.status_lbl.setText(f"Erro: {msg}")

        run_async(fetch, done, fail)

    def _populate(self, r: dict):
        # ── CPU / Memória ── #
        cpu = parsers.parse_cpu_usage(r["cpu"])
        mem = parsers.parse_memory_usage(r["mem"])
        # limpa grade
        while self._cpu_grid.count():
            item = self._cpu_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (lbl, val) in enumerate([
                ("Uso de CPU", cpu), ("Uso de Memória", mem)]):
            l = QLabel(f"<b>{lbl}:</b>")
            v = QLabel(val)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._cpu_grid.addWidget(l, i, 0)
            self._cpu_grid.addWidget(v, i, 1)
        cpu_clean = _clean_or_na(r["cpu"])
        mem_clean = _clean_or_na(r["mem"])
        details = ""
        if cpu_clean:
            details += f"=== display cpu-usage ===\n{cpu_clean}\n\n"
        if mem_clean:
            details += f"=== display memory-usage ===\n{mem_clean}"
        self._cpu_raw.setPlainText(details or "Sem dados detalhados.")

        # ── Chassi / Fans / Energia ── #
        dev = _clean_or_na(r["device"])
        self._hw_raw.setPlainText(
            dev if dev is not None
            else "Comando 'display device' não suportado neste equipamento.")

        # ── Temperatura ── #
        env = _clean_or_na(r["env"])
        if env is None:
            self._env_table.setRowCount(0)
            self._env_notice.setText(
                "Comando 'display environment' não suportado neste equipamento.")
        else:
            rows = self._parse_env(env)
            if rows:
                self._set_table_rows(self._env_table, rows)
                self._env_notice.setText("")
            else:
                self._env_table.setRowCount(0)
                self._env_notice.setText(
                    "Nenhum dado de temperatura encontrado.\n\n" + env[:400])

        # ── Transceivers ── #
        opt = _clean_or_na(r["optical"])
        if opt is None:
            self._opt_table.setRowCount(0)
            self._opt_notice.setText(
                "Comando 'display transceiver all' não suportado "
                "neste equipamento.")
        else:
            rows = self._parse_optical(opt)
            if rows:
                self._set_table_rows(self._opt_table, rows)
                self._opt_notice.setText("")
            else:
                self._opt_table.setRowCount(0)
                self._opt_notice.setText(
                    "Nenhum transceptor ativo encontrado.")

    # ── parsers internos ── #

    @staticmethod
    def _parse_env(text: str) -> list[list[str]]:
        """Extrai linhas de temperatura do output de 'display environment'."""
        rows = []
        # VRP: ex: "MainBoard 0 Temperature : 45 C   Upper Limit : 90 C   Lower Limit : -40 C"
        pat = re.compile(
            r"(\S.*?)\s+Temperature\s*:\s*([\d.]+\s*C?)"
            r"(?:.*?Upper\s+Limit\s*:\s*([\d.]+\s*C?))?"
            r"(?:.*?Lower\s+Limit\s*:\s*([\d.-]+\s*C?))?",
            re.IGNORECASE)
        for line in text.splitlines():
            m = pat.search(line)
            if m:
                rows.append([
                    m.group(1).strip(),
                    m.group(2).strip(),
                    m.group(3).strip() if m.group(3) else "—",
                    m.group(4).strip() if m.group(4) else "—",
                ])
        return rows

    @staticmethod
    def _parse_optical(text: str) -> list[list[str]]:
        """Extrai dados de transceivers do output de 'display transceiver all'."""
        rows = []
        # Acumula por interface
        current_iface = ""
        tr_type = tx = rx = temp = bias = ""

        iface_re = re.compile(
            r"^((?:GigabitEthernet|XGigabitEthernet|100GE|40GE|10GE|Eth-Trunk|"
            r"GE|XGE)\S+)\s*$", re.IGNORECASE)
        kv_re = re.compile(
            r"(Transceiver Type|TX Power|RX Power|Temperature|Bias Current"
            r"|Current Rx Power|Current Tx Power)\s*:\s*(.+)",
            re.IGNORECASE)

        def flush():
            if current_iface and any([tr_type, tx, rx, temp, bias]):
                rows.append([current_iface, tr_type, tx, rx, temp, bias])

        for line in text.splitlines():
            mi = iface_re.match(line.strip())
            if mi:
                flush()
                current_iface = mi.group(1)
                tr_type = tx = rx = temp = bias = ""
                continue
            mk = kv_re.search(line)
            if mk:
                key = mk.group(1).lower()
                val = mk.group(2).strip()
                if "type" in key:
                    tr_type = val
                elif "tx" in key:
                    tx = val
                elif "rx" in key:
                    rx = val
                elif "temp" in key:
                    temp = val
                elif "bias" in key or "current" in key:
                    bias = val
        flush()
        return rows
