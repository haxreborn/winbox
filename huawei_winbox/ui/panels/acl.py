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
            ("Regra básica...", self._add_rule),
            ("Regra avançada...", self._add_advanced_rule),
            ("ACL nomeada...", self._named_acl),
            ("Aplicar interface...", self._apply_iface),
            ("Traffic policy...", self._traffic_policy),
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
            "Nova regra básica de ACL",
            [("acl_number",
              "Número da ACL (2000-2999 básica, 3000-3999 avançada)",
              "int", 2000),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("source", "Origem (IP ou 'any')", "text", "any"),
             ("wildcard", "Curinga origem (ex.: 0.0.0.255)", "text", ""),
             ("rule_id", "ID da regra (0 = automático)", "int", 0)],
            self)
        if values:
            self.apply_commands(translator.acl_rule(
                values["acl_number"], values["action"], values["source"],
                values["wildcard"], rule_id=values["rule_id"]))

    def _add_advanced_rule(self):
        values = SimpleForm.ask(
            "Nova regra avançada de ACL (3000-3999)",
            [("acl_number", "Número da ACL avançada", "int", 3000),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("protocol",
              "Protocolo",
              "choice:ip|tcp|udp|icmp|ospf|gre",
              "ip"),
             ("src_ip", "IP de origem (ou 'any')", "text", "any"),
             ("src_wildcard", "Curinga de origem", "text", ""),
             ("dst_ip", "IP de destino (ou 'any')", "text", "any"),
             ("dst_wildcard", "Curinga de destino", "text", ""),
             ("src_port", "Porta de origem (0 = qualquer)", "int", 0),
             ("dst_port", "Porta de destino (0 = qualquer)", "int", 0),
             ("rule_id", "ID da regra (0 = automático)", "int", 0)],
            self)
        if values:
            self.apply_commands(translator.acl_advanced_rule(
                values["acl_number"], values["action"], values["protocol"],
                values["src_ip"], values["src_wildcard"],
                values["dst_ip"], values["dst_wildcard"],
                src_port=values["src_port"] or None,
                dst_port=values["dst_port"] or None,
                rule_id=values["rule_id"] or None))

    def _named_acl(self):
        values = SimpleForm.ask(
            "ACL Nomeada",
            [("name", "Nome da ACL", "text", ""),
             ("acl_type",
              "Tipo",
              "choice:advance|basic|link",
              "advance"),
             ("remove", "Remover ACL nomeada", "bool", False)],
            self)
        if values and values["name"]:
            self.apply_commands(translator.acl_named(
                values["name"], values["acl_type"],
                remove=values["remove"]))

    def _remove_rule(self):
        rows = self.require_selection("uma ou mais regras")
        if rows:
            self.apply_for_each(
                rows, lambda r: translator.acl_rule(
                    int(r["acl"]), "", rule_id=int(r["rule"]), remove=True))

    def _remove_acl(self):
        rows = self.require_selection("uma regra da ACL a remover")
        if not rows:
            return
        # Várias regras selecionadas podem pertencer à mesma ACL; apagar a
        # ACL duas vezes daria erro na segunda.
        acls = sorted({int(r["acl"]) for r in rows})
        if QMessageBox.question(
                self, self.title,
                f"Remover {'a ACL' if len(acls) == 1 else 'as ACLs'} "
                f"{', '.join(str(a) for a in acls)} por inteiro, com todas "
                f"as suas regras?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.apply_for_each([{"acl": a} for a in acls],
                            lambda r: translator.acl_delete(r["acl"]))

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

    def _traffic_policy(self):
        values = SimpleForm.ask(
            "Aplicar Traffic Policy na interface",
            [("interface", "Interface", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound"),
             ("policy_name", "Nome da traffic policy", "text", ""),
             ("remove", "Remover policy", "bool", False)],
            self)
        if values and values["interface"] and values["policy_name"]:
            self.apply_commands(translator.traffic_policy_interface(
                values["interface"], values["direction"],
                values["policy_name"], remove=values["remove"]))
