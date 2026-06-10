"""Painel Vizinhos — descoberta de equipamentos conectados via LLDP
(equivalente ao *Neighbors* do WinBox)."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from .base import BasePanel


class LldpPanel(BasePanel):
    title = "Vizinhos (LLDP)"
    columns = ["Interface local", "Equipamento vizinho",
               "Interface do vizinho", "Expira (s)"]

    def build_toolbar(self, layout):
        on_btn = QPushButton("Habilitar LLDP")
        on_btn.clicked.connect(
            lambda: self.apply_commands(translator.lldp_enable(True)))
        layout.addWidget(on_btn)
        off_btn = QPushButton("Desabilitar LLDP")
        off_btn.setObjectName("dangerBtn")
        off_btn.clicked.connect(
            lambda: self.apply_commands(translator.lldp_enable(False)))
        layout.addWidget(off_btn)

    def fetch(self):
        return parsers.parse_lldp_neighbors(
            self.session.run("display lldp neighbor brief"))

    def row_values(self, row):
        return [row["local"], row["neighbor_dev"],
                row["neighbor_intf"], row["exptime"]]
