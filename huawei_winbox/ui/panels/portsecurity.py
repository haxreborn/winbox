"""Painel Segurança de Porta — port-security, DHCP snooping, ARP inspection."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class PortSecurityPanel(BasePanel):
    title = "Segurança L2"
    columns = ["Interface", "Seg. porta", "Máx MACs", "Ação", "DHCP Snoop"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Seg. porta...", self._port_sec),
            ("DHCP Snooping global", self._dhcp_snoop_global),
            ("DHCP Snoop iface...", self._dhcp_snoop_iface),
            ("ARP Inspection VLAN...", self._arp_insp),
            ("MAC estático...", self._static_mac),
            ("Storm control...", self._storm),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_port_security(
            self.session.run("display port-security"))

    def row_values(self, row):
        return [row["interface"], row["enabled"], row["max_mac"],
                row["action"], row["dhcp_snooping"]]

    def _port_sec(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Segurança de porta",
            [("interface", "Interface", "text",
              row["interface"] if row else ""),
             ("enable", "Habilitar", "bool", True),
             ("max_mac", "Máx MACs (0=não alterar)", "int", 1),
             ("action", "Ação violação",
              "choice:restrict|protect|shutdown", "restrict")],
            self)
        if not values or not values["interface"]:
            return
        cmds = translator.port_security_enable(
            values["interface"], values["enable"])
        if values["enable"] and values["max_mac"]:
            cmds += translator.port_security_max_mac(
                values["interface"], values["max_mac"])
            cmds += translator.port_security_action(
                values["interface"], values["action"])
        self.apply_commands(cmds)

    def _dhcp_snoop_global(self):
        values = SimpleForm.ask(
            "DHCP Snooping global",
            [("enable", "Habilitar", "bool", True)],
            self)
        if values is not None:
            self.apply_commands(
                translator.dhcp_snooping_enable(values["enable"]))

    def _dhcp_snoop_iface(self):
        values = SimpleForm.ask(
            "DHCP Snooping — confiança de porta",
            [("interface", "Interface", "text", ""),
             ("trusted", "Porta confiável (uplink)", "bool", True)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.dhcp_snooping_interface(
                values["interface"], values["trusted"]))

    def _arp_insp(self):
        values = SimpleForm.ask(
            "Dynamic ARP Inspection",
            [("vlan_id", "VLAN", "int", 1),
             ("enable", "Habilitar", "bool", True),
             ("interface", "Interface confiável (uplink, opcional)", "text", "")],
            self)
        if values is not None:
            cmds = translator.arp_inspection_vlan(
                values["vlan_id"], values["enable"])
            if values["interface"]:
                cmds += translator.arp_inspection_interface(
                    values["interface"], trusted=True)
            self.apply_commands(cmds)

    def _static_mac(self):
        values = SimpleForm.ask(
            "MAC estático de segurança",
            [("interface", "Interface", "text", ""),
             ("mac", "Endereço MAC (xxxx-xxxx-xxxx)", "text", ""),
             ("vlan", "VLAN", "int", 1),
             ("remove", "Remover", "bool", False)],
            self)
        if values and values["interface"] and values["mac"]:
            self.apply_commands(translator.port_security_static_mac(
                values["interface"], values["mac"],
                values["vlan"], remove=values["remove"]))

    def _storm(self):
        values = SimpleForm.ask(
            "Storm Control",
            [("interface", "Interface", "text", ""),
             ("type", "Tipo",
              "choice:broadcast|multicast|unknown-unicast", "broadcast"),
             ("min_rate", "Taxa mínima (pps)", "int", 100),
             ("max_rate", "Taxa máxima (pps)", "int", 1000),
             ("remove", "Remover", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.storm_control(
                values["interface"], values["type"],
                values["min_rate"], values["max_rate"],
                remove=values["remove"]))
