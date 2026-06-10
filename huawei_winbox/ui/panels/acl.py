"""Painel ACL / Firewall — regras de filtragem e aplicação em interfaces."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class AclPanel(BasePanel):
    title = "ACL / Firewall"
    columns = ["ACL", "Tipo", "Regra", "Ação", "Detalhe"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Nova regra...", self._add_rule),
            ("Aplicar na interface...", self._apply_iface),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_rule = QPushButton("Remover regra")
        del_rule.setObjectName("dangerBtn")
        del_rule.clicked.connect(self._remove_rule)
        layout.addWidget(del_rule)
        del_acl = QPushButton("Remover ACL inteira")
        del_acl.setObjectName("dangerBtn")
        del_acl.clicked.connect(self._remove_acl)
        layout.addWidget(del_acl)

    def fetch(self):
        return parsers.parse_acls(self.session.run("display acl all"))

    def row_values(self, row):
        return [row["acl"], row["type"], row["rule"],
                row["action"], row["detail"]]

    def _add_rule(self):
        values = SimpleForm.ask(
            "Nova regra de ACL",
            [("acl_number", "Número da ACL (2000-2999 básica, "
                            "3000-3999 avançada)", "int", 2000),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("source", "Origem (IP ou 'any')", "text", "any"),
             ("wildcard", "Curinga (ex.: 0.0.0.255)", "text", ""),
             ("rule_id", "ID da regra (0 = automático)", "int", 0)],
            self)
        if values:
            self.apply_commands(translator.acl_rule(
                values["acl_number"], values["action"], values["source"],
                values["wildcard"], rule_id=values["rule_id"]))

    def _remove_rule(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione uma regra.")
            return
        self.apply_commands(translator.acl_rule(
            int(row["acl"]), "", rule_id=int(row["rule"]), remove=True))

    def _remove_acl(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title,
                                    "Selecione uma regra da ACL a remover.")
            return
        self.apply_commands(translator.acl_delete(int(row["acl"])))

    def _apply_iface(self):
        values = SimpleForm.ask(
            "Aplicar ACL na interface (traffic-filter)",
            [("interface", "Interface", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound"),
             ("acl_number", "Número da ACL", "int", 2000),
             ("remove", "Remover o filtro (undo)", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.traffic_filter(
                values["interface"], values["direction"],
                values["acl_number"], remove=values["remove"]))
