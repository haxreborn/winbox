"""Painel de Rotas — tabela de roteamento e rotas estáticas."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class RoutesPanel(BasePanel):
    title = "Rotas"
    columns = ["Destino", "Protocolo", "Pref", "Custo",
               "Próximo salto", "Interface"]

    def build_toolbar(self, layout):
        add_btn = QPushButton("Rota estática...")
        add_btn.clicked.connect(self._add_static)
        layout.addWidget(add_btn)
        del_btn = QPushButton("Remover estática")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove_static)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_routing_table(
            self.session.run("display ip routing-table"))

    def row_values(self, row):
        return [row["destination"], row["proto"], row["pre"],
                row["cost"], row["nexthop"], row["interface"]]

    def _add_static(self):
        values = SimpleForm.ask(
            "Nova rota estática",
            [("destination", "Rede de destino (ex.: 10.0.0.0)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("next_hop", "Próximo salto", "text", ""),
             ("preference", "Preferência (0 = padrão 60)", "int", 0),
             ("description", "Descrição (opcional)", "text", "")],
            self)
        if values and values["destination"] and values["next_hop"]:
            self.apply_commands(translator.static_route(
                values["destination"], values["mask"], values["next_hop"],
                preference=values["preference"] or None,
                description=values["description"]))

    def _remove_static(self):
        row = self.selected_row()
        if not row or row["proto"].lower() != "static":
            QMessageBox.information(
                self, self.title,
                "Selecione uma rota do tipo Static para remover.")
            return
        destination, prefix = row["destination"].split("/", 1)
        self.apply_commands(translator.static_route(
            destination, prefix, row["nexthop"], remove=True))
