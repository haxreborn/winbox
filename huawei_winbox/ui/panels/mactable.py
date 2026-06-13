"""Painel Tabela MAC — display mac-address-table e entradas estáticas."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox, QLineEdit, QLabel

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class MacTablePanel(BasePanel):
    title = "Tabela MAC"
    columns = ["MAC", "VLAN", "Tipo", "Interface"]

    def build_toolbar(self, layout):
        layout.addWidget(QLabel("VLAN:"))
        self.vlan_edit = QLineEdit()
        self.vlan_edit.setPlaceholderText("(todas)")
        self.vlan_edit.setMaximumWidth(70)
        self.vlan_edit.returnPressed.connect(self.refresh)
        layout.addWidget(self.vlan_edit)

        layout.addWidget(QLabel("MAC:"))
        self.mac_edit = QLineEdit()
        self.mac_edit.setPlaceholderText("(todos)")
        self.mac_edit.setMaximumWidth(130)
        self.mac_edit.returnPressed.connect(self.refresh)
        layout.addWidget(self.mac_edit)

        for text, slot in [
            ("Buscar", self.refresh),
            ("MAC estático...", self._add_static),
            ("MAC blackhole...", self._add_blackhole),
            ("Aging time...", self._set_aging),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        flush_btn = QPushButton("Limpar tabela")
        flush_btn.setObjectName("dangerBtn")
        flush_btn.clicked.connect(self._flush)
        layout.addWidget(flush_btn)

    def refresh(self):
        self._vlan_filter = self.vlan_edit.text().strip()
        self._mac_filter = self.mac_edit.text().strip()
        super().refresh()

    def fetch(self):
        if self._mac_filter:
            cmd = f"display mac-address {self._mac_filter}"
        elif self._vlan_filter:
            cmd = f"display mac-address vlan {self._vlan_filter}"
        else:
            cmd = "display mac-address"
        return parsers.parse_mac_table(self.session.run(cmd, timeout=30.0))

    def row_values(self, row):
        return [row["mac"], row["vlan"], row["type"], row["interface"]]

    def _add_static(self):
        values = SimpleForm.ask(
            "Entrada MAC estática",
            [("mac", "MAC (xxxx-xxxx-xxxx)", "text", ""),
             ("vlan", "VLAN", "int", 1),
             ("interface", "Interface", "text", ""),
             ("remove", "Remover", "bool", False)],
            self)
        if values and values["mac"] and values["interface"]:
            self.apply_commands(translator.mac_static(
                values["mac"], values["vlan"],
                values["interface"], remove=values["remove"]),
                system_view=False)

    def _add_blackhole(self):
        values = SimpleForm.ask(
            "MAC Blackhole (descarta tráfego)",
            [("mac", "MAC (xxxx-xxxx-xxxx)", "text", ""),
             ("vlan", "VLAN", "int", 1),
             ("remove", "Remover", "bool", False)],
            self)
        if values and values["mac"]:
            self.apply_commands(translator.mac_blackhole(
                values["mac"], values["vlan"], remove=values["remove"]),
                system_view=False)

    def _set_aging(self):
        values = SimpleForm.ask(
            "Tempo de aging da tabela MAC",
            [("seconds", "Segundos (30-1000000)", "int", 300)],
            self)
        if values:
            self.apply_commands(translator.mac_aging_time(values["seconds"]),
                                system_view=False)

    def _flush(self):
        values = SimpleForm.ask(
            "Limpar tabela MAC",
            [("scope", "Escopo",
              "choice:toda a tabela|por VLAN|por interface", "toda a tabela"),
             ("vlan", "VLAN (se por VLAN)", "int", 0),
             ("interface", "Interface (se por interface)", "text", "")],
            self)
        if values is None:
            return
        if values["scope"] == "por VLAN" and values["vlan"]:
            cmds = translator.mac_flush(vlan=values["vlan"])
        elif values["scope"] == "por interface" and values["interface"]:
            cmds = translator.mac_flush(interface=values["interface"])
        else:
            cmds = translator.mac_flush()
        self.apply_commands(cmds, system_view=False)
