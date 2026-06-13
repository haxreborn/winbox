"""Painel IPv6 — endereços, rotas estáticas e tabela de roteamento."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class Ipv6Panel(BasePanel):
    title = "IPv6"
    columns = ["Interface / Destino", "Endereço / Próximo salto",
               "Prefixo", "Estado / Proto"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar IPv6 na iface...", self._enable_iface),
            ("Adicionar endereço...", self._add_addr),
            ("Remover endereço...", self._del_addr),
            ("Rota estática IPv6...", self._add_route),
            ("Remover rota...", self._del_route),
            ("Ver tabela IPv6", self._show_table),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        rows = []
        try:
            rows += parsers.parse_ipv6_interfaces(
                self.session.run("display ipv6 interface brief"))
        except Exception:
            pass
        try:
            rows += parsers.parse_ipv6_routing(
                self.session.run("display ipv6 routing-table"))
        except Exception:
            pass
        return rows

    def row_values(self, row):
        return [row["name"], row["address"],
                row.get("prefix", ""), row.get("state", "")]

    def _enable_iface(self):
        values = SimpleForm.ask(
            "Habilitar IPv6 na interface",
            [("interface", "Interface", "text", ""),
             ("enable", "Habilitar", "bool", True)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.ipv6_interface_enable(
                values["interface"], values["enable"]))

    def _add_addr(self):
        values = SimpleForm.ask(
            "Adicionar endereço IPv6",
            [("interface", "Interface", "text", ""),
             ("address", "Endereço IPv6", "text", ""),
             ("prefix_len", "Comprimento do prefixo", "int", 64),
             ("link_local", "Link-local", "bool", False)],
            self)
        if values and values["interface"] and values["address"]:
            self.apply_commands(translator.ipv6_address(
                values["interface"], values["address"],
                values["prefix_len"], values["link_local"]))

    def _del_addr(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Remover endereço IPv6",
            [("interface", "Interface", "text",
              row["name"] if row else ""),
             ("address", "Endereço IPv6", "text",
              row["address"].split("/")[0] if row else ""),
             ("prefix_len", "Comprimento do prefixo", "int", 64),
             ("link_local", "Link-local", "bool", False)],
            self)
        if values and values["interface"] and values["address"]:
            self.apply_commands(translator.ipv6_address(
                values["interface"], values["address"],
                values["prefix_len"], values["link_local"], remove=True))

    def _add_route(self):
        values = SimpleForm.ask(
            "Rota estática IPv6",
            [("destination", "Destino (ex.: 2001:db8::/32)", "text", ""),
             ("prefix_len", "Prefixo", "int", 64),
             ("next_hop", "Próximo salto", "text", ""),
             ("preference", "Preferência (0 = padrão)", "int", 0)],
            self)
        if values and values["destination"] and values["next_hop"]:
            self.apply_commands(translator.ipv6_static_route(
                values["destination"], values["prefix_len"],
                values["next_hop"],
                preference=values["preference"] or None))

    def _del_route(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Remover rota IPv6",
            [("destination", "Destino", "text",
              row["name"] if row else ""),
             ("prefix_len", "Prefixo", "int", 64),
             ("next_hop", "Próximo salto", "text",
              row["address"] if row else "")],
            self)
        if values and values["destination"] and values["next_hop"]:
            self.apply_commands(translator.ipv6_static_route(
                values["destination"], values["prefix_len"],
                values["next_hop"], remove=True))

    def _show_table(self):
        self.refresh()
