"""Painel STP/RSTP/MSTP — spanning tree global e por porta."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class StpPanel(BasePanel):
    title = "STP / MSTP"
    columns = ["Instância", "VLAN", "Prioridade", "Role", "Estado", "Interface"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Modo STP...", self._set_mode),
            ("Prioridade global...", self._set_priority),
            ("Região MSTP...", self._set_region),
            ("Inst. → VLANs...", self._map_instance),
            ("Config porta...", self._iface_config),
            ("Habilitar STP", lambda: self.apply_commands(translator.stp_enable(True))),
            ("Desabilitar STP", lambda: self.apply_commands(translator.stp_enable(False))),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_stp_brief(
            self.session.run("display stp brief"))

    def row_values(self, row):
        return [row["instance"], row["vlan"], row["priority"],
                row["role"], row["state"], row["interface"]]

    def _set_mode(self):
        values = SimpleForm.ask(
            "Modo Spanning Tree",
            [("mode", "Modo", "choice:mstp|rstp|stp", "mstp")],
            self)
        if values:
            self.apply_commands(translator.stp_mode(values["mode"]))

    def _set_priority(self):
        values = SimpleForm.ask(
            "Prioridade global STP/MSTP",
            [("priority", "Prioridade (múltiplo de 4096)", "int", 32768),
             ("instance", "Instância MSTP (0 = global)", "int", 0)],
            self)
        if values is not None:
            self.apply_commands(translator.stp_priority(
                values["priority"], values["instance"]))

    def _set_region(self):
        values = SimpleForm.ask(
            "Região MSTP",
            [("region_name", "Nome da região", "text", ""),
             ("revision", "Revisão", "int", 0)],
            self)
        if values and values["region_name"]:
            self.apply_commands(translator.mstp_region(
                values["region_name"], values["revision"]))

    def _map_instance(self):
        values = SimpleForm.ask(
            "Mapear VLANs para instância MSTP",
            [("instance", "Instância", "int", 1),
             ("vlan_range", "VLANs (ex.: 10 20 to 30)", "text", ""),
             ("remove", "Remover mapeamento", "bool", False)],
            self)
        if values and values["vlan_range"]:
            self.apply_commands(translator.mstp_instance_vlan(
                values["instance"], values["vlan_range"],
                remove=values["remove"]))

    def _iface_config(self):
        row = self.selected_row()
        iface = row["interface"] if row else ""
        values = SimpleForm.ask(
            "Configuração STP da porta",
            [("interface", "Interface", "text", iface),
             ("edge", "Edge port (PortFast)", "choice:sim|não|padrão", "padrão"),
             ("bpdu_guard", "BPDU Guard", "choice:sim|não|padrão", "padrão"),
             ("bpdu_filter", "BPDU Filter", "choice:sim|não|padrão", "padrão"),
             ("root_guard", "Root Guard", "choice:sim|não|padrão", "padrão"),
             ("cost", "Custo (0 = automático)", "int", 0),
             ("priority", "Prioridade da porta (0 = padrão)", "int", 0)],
            self)
        if not values or not values["interface"]:
            return
        def _tri(v):
            if v == "sim":
                return True
            if v == "não":
                return False
            return None
        self.apply_commands(translator.stp_interface(
            values["interface"],
            edge=_tri(values["edge"]),
            bpdu_guard=_tri(values["bpdu_guard"]),
            bpdu_filter=_tri(values["bpdu_filter"]),
            root_guard=_tri(values["root_guard"]),
            cost=values["cost"] or None,
            priority=values["priority"] or None,
        ))
