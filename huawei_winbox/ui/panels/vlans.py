"""Painel de VLANs (criação, remoção e modo de porta access/trunk)."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class VlansPanel(BasePanel):
    title = "VLANs"
    columns = ["VLAN ID", "Tipo", "Descrição"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Criar VLAN...", self._create),
            ("Porta Access...", self._port_access),
            ("Porta Trunk...", self._port_trunk),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_vlans(self.session.run("display vlan"))

    def row_values(self, row):
        return [row["id"], row["type"], row["description"]]

    def _create(self):
        values = SimpleForm.ask(
            "Nova VLAN",
            [("vlan_id", "VLAN ID (1-4094)", "int", 10),
             ("name", "Nome (opcional)", "text", "")],
            self)
        if values:
            self.apply_commands(
                translator.vlan_create(values["vlan_id"], values["name"]))

    def _delete(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione uma VLAN.")
            return
        self.apply_commands(translator.vlan_delete(int(row["id"])))

    def _port_access(self):
        values = SimpleForm.ask(
            "Porta em modo access",
            [("interface", "Interface", "text", ""),
             ("vlan_id", "VLAN ID", "int", 10)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.port_access(
                values["interface"], values["vlan_id"]))

    def _port_trunk(self):
        values = SimpleForm.ask(
            "Porta em modo trunk",
            [("interface", "Interface", "text", ""),
             ("vlans", "VLANs permitidas (ex.: 10 20 30 ou 10 to 20)",
              "text", "")],
            self)
        if values and values["interface"] and values["vlans"]:
            self.apply_commands(translator.port_trunk(
                values["interface"], values["vlans"]))
