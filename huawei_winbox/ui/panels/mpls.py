"""Painel MPLS — sessões LDP e habilitação de MPLS nas interfaces."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class MplsPanel(BasePanel):
    title = "MPLS / LDP"
    columns = ["Peer LDP", "Status", "Papel", "Tempo ativo"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar MPLS+LDP...", self._enable),
            ("MPLS na interface...", self._iface),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_ldp_sessions(
            self.session.run("display mpls ldp session"))

    def row_values(self, row):
        return [row["peer"], row["status"], row["role"], row["age"]]

    def _enable(self):
        values = SimpleForm.ask(
            "Habilitar MPLS e LDP globalmente",
            [("lsr_id", "LSR ID (geralmente o IP da loopback)", "text", "")],
            self)
        if values and values["lsr_id"]:
            self.apply_commands(translator.mpls_enable(values["lsr_id"]))

    def _iface(self):
        values = SimpleForm.ask(
            "MPLS na interface",
            [("interface", "Interface", "text", ""),
             ("ldp", "Habilitar também o LDP", "bool", True),
             ("remove", "Remover (undo)", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.mpls_interface(
                values["interface"], ldp=values["ldp"],
                remove=values["remove"]))
