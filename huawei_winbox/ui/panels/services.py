"""Painel Serviços — NTP, SNMP, SSH e LLDP num só lugar."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class ServicesPanel(BasePanel):
    title = "Serviços"
    columns = ["Serviço", "Estado"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Servidor NTP...", self._ntp),
            ("Comunidade SNMP...", self._snmp),
            ("Habilitar SSH", self._ssh),
            ("Habilitar LLDP", self._lldp),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        ntp = parsers.parse_ntp_status(
            self.session.run("display ntp-service status"))
        ssh = parsers.parse_ssh_server_status(
            self.session.run("display ssh server status"))
        lldp = parsers.parse_lldp_status(
            self.session.run("display lldp local"))
        return [
            {"item": "NTP (sincronia do relógio)", "valor": ntp},
            {"item": "SSH (STelnet server)", "valor": ssh},
            {"item": "LLDP (descoberta de vizinhos)", "valor": lldp},
        ]

    def row_values(self, row):
        return [row["item"], row["valor"]]

    def _ntp(self):
        values = SimpleForm.ask(
            "Servidor NTP",
            [("ip", "IP do servidor NTP", "text", ""),
             ("remove", "Remover (undo)", "bool", False)],
            self)
        if values and values["ip"]:
            self.apply_commands(translator.ntp_server(
                values["ip"], remove=values["remove"]))

    def _snmp(self):
        values = SimpleForm.ask(
            "Comunidade SNMP (v2c)",
            [("community", "Nome da comunidade", "text", ""),
             ("write", "Permitir escrita (write)", "bool", False),
             ("remove", "Remover (undo)", "bool", False)],
            self)
        if values and values["community"]:
            self.apply_commands(translator.snmp_community(
                values["community"], write=values["write"],
                remove=values["remove"]))

    def _ssh(self):
        self.apply_commands(translator.ssh_server_enable())

    def _lldp(self):
        self.apply_commands(translator.lldp_enable(True))
