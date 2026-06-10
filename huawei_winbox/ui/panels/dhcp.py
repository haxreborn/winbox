"""Painel DHCP — pools de endereços e ativação por interface."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class DhcpPanel(BasePanel):
    title = "Servidor DHCP"
    columns = ["Pool", "Rede", "Gateway", "Em uso", "Livres"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Novo pool...", self._add_pool),
            ("Ativar na interface...", self._iface),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover pool")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete_pool)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_ip_pools(self.session.run("display ip pool"))

    def row_values(self, row):
        return [row["name"], row["network"], row["gateway"],
                row["used"], row["idle"]]

    def _add_pool(self):
        values = SimpleForm.ask(
            "Novo pool DHCP",
            [("name", "Nome do pool", "text", ""),
             ("network", "Rede (ex.: 192.168.10.0)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("gateway", "Gateway (opcional)", "text", ""),
             ("dns", "Servidor DNS (opcional)", "text", ""),
             ("lease_days", "Duração do lease (dias)", "int", 1)],
            self)
        if values and values["name"] and values["network"]:
            self.apply_commands(translator.dhcp_pool(
                values["name"], values["network"], values["mask"],
                values["gateway"], values["dns"], values["lease_days"]))

    def _delete_pool(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um pool.")
            return
        self.apply_commands(translator.dhcp_pool_delete(row["name"]))

    def _iface(self):
        values = SimpleForm.ask(
            "DHCP na interface",
            [("interface", "Interface (ex.: Vlanif10)", "text", ""),
             ("remove", "Desativar (undo)", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.dhcp_interface(
                values["interface"], remove=values["remove"]))
