"""Painel OSPF — vizinhanças e configuração de áreas/redes."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class OspfPanel(BasePanel):
    title = "OSPF"
    columns = ["Área", "Interface", "Vizinho", "Estado"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar OSPF...", self._enable),
            ("Adicionar rede...", self._add_network),
            ("Remover rede...", self._remove_network),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_ospf_peers(
            self.session.run("display ospf peer brief"))

    def row_values(self, row):
        return [row["area"], row["interface"], row["neighbor"], row["state"]]

    def _enable(self):
        values = SimpleForm.ask(
            "Habilitar processo OSPF",
            [("process_id", "ID do processo", "int", 1),
             ("router_id", "Router ID (opcional)", "text", "")],
            self)
        if values:
            self.apply_commands(translator.ospf_enable(
                values["process_id"], values["router_id"]))

    def _network_form(self, title: str) -> dict | None:
        return SimpleForm.ask(
            title,
            [("process_id", "ID do processo", "int", 1),
             ("area", "Área (ex.: 0 ou 0.0.0.0)", "text", "0"),
             ("network", "Rede (ex.: 10.0.0.0)", "text", ""),
             ("wildcard", "Curinga/wildcard (ex.: 0.0.0.255)", "text", "")],
            self)

    def _add_network(self):
        values = self._network_form("Adicionar rede ao OSPF")
        if values and values["network"]:
            self.apply_commands(translator.ospf_network(
                values["process_id"], values["area"],
                values["network"], values["wildcard"]))

    def _remove_network(self):
        values = self._network_form("Remover rede do OSPF")
        if values and values["network"]:
            self.apply_commands(translator.ospf_network(
                values["process_id"], values["area"],
                values["network"], values["wildcard"], remove=True))
