"""Painel ARP — tabela de vizinhança IP/MAC e entradas estáticas."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class ArpPanel(BasePanel):
    title = "ARP"
    columns = ["Endereço IP", "Endereço MAC", "Expira (min)",
               "Tipo", "Interface"]

    def build_toolbar(self, layout):
        add_btn = QPushButton("ARP estático...")
        add_btn.clicked.connect(self._add_static)
        layout.addWidget(add_btn)
        del_btn = QPushButton("Remover estático")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove_static)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_arp(self.session.run("display arp"))

    def row_values(self, row):
        return [row["ip"], row["mac"], row["expire"],
                row["type"], row["interface"]]

    def _add_static(self):
        values = SimpleForm.ask(
            "Nova entrada ARP estática",
            [("ip", "Endereço IP", "text", ""),
             ("mac", "Endereço MAC (xxxx-xxxx-xxxx)", "text", "")],
            self)
        if values and values["ip"] and values["mac"]:
            self.apply_commands(translator.arp_static(
                values["ip"], values["mac"]))

    def _remove_static(self):
        rows = self.require_selection("uma ou mais entradas")
        if rows:
            self.apply_for_each(
                rows, lambda r: translator.arp_static(r["ip"], r["mac"],
                                                      remove=True))
