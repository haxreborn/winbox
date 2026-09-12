"""Painel de Endereços IP — como *IP > Addresses* do WinBox."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class AddressesPanel(BasePanel):
    title = "Endereços IP"
    columns = ["Interface", "Endereço IP", "Físico", "Protocolo"]

    def build_toolbar(self, layout):
        add_btn = QPushButton("Adicionar...")
        add_btn.clicked.connect(self._add)
        layout.addWidget(add_btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_ip_interface_brief(
            self.session.run("display ip interface brief"))

    def row_values(self, row):
        return [row["name"], row["ip"], row["physical"], row["protocol"]]

    def _add(self):
        values = SimpleForm.ask(
            "Novo endereço IP",
            [("interface", "Interface (ex.: GigabitEthernet0/0/1)", "text", ""),
             ("address", "Endereço IP", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", "")],
            self)
        if values and values["interface"] and values["address"]:
            self.apply_commands(translator.interface_ip(
                values["interface"], values["address"], values["mask"]))

    def _remove(self):
        rows = [r for r in self.require_selection("uma ou mais linhas")
                if "/" in r["ip"]]
        if not rows:
            QMessageBox.information(
                self, self.title,
                "Nenhuma das linhas selecionadas tem endereço IP atribuído.")
            return

        def build(row):
            address, prefix = row["ip"].split("/", 1)
            return translator.interface_ip(row["name"], address, prefix,
                                           remove=True)

        self.apply_for_each(rows, build)
