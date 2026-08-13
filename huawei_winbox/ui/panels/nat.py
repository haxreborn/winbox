"""Painel NAT — masquerade (easy-ip) e redirecionamento de portas."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class NatPanel(BasePanel):
    title = "NAT"
    columns = ["Tipo", "Interface", "ACL / Protocolo",
               "Global", "Interno"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("NAT de saída...", self._outbound),
            ("Redirecionar porta...", self._server),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove)
        layout.addWidget(del_btn)

    def fetch(self):
        rows = []
        for r in parsers.parse_nat_outbound(
                self.session.run("display nat outbound")):
            rows.append({"kind": "outbound", "interface": r["interface"],
                         "info": r["acl"], "global": r["target"],
                         "inside": r["type"], "raw": r})
        for r in parsers.parse_nat_servers(
                self.session.run("display nat server")):
            rows.append({"kind": "server", "interface": r["interface"],
                         "info": r["protocol"], "global": r["global"],
                         "inside": r["inside"], "raw": r})
        return rows

    def row_values(self, row):
        kind = "Saída (easy-ip)" if row["kind"] == "outbound" \
            else "Porta (server)"
        return [kind, row["interface"], row["info"],
                row["global"], row["inside"]]

    def _outbound(self):
        values = SimpleForm.ask(
            "NAT de saída (easy-ip)",
            [("interface", "Interface de saída (WAN)", "text", ""),
             ("acl", "ACL com as redes internas (ex.: 2000)", "int", 2000)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.nat_outbound(
                values["interface"], values["acl"]))

    def _server(self):
        values = SimpleForm.ask(
            "Redirecionamento de porta (NAT server)",
            [("interface", "Interface de entrada (WAN)", "text", ""),
             ("protocol", "Protocolo", "choice:tcp|udp", "tcp"),
             ("global_ip", "IP global (vazio = IP da interface)", "text", ""),
             ("global_port", "Porta externa", "int", 8080),
             ("inside_ip", "IP interno", "text", ""),
             ("inside_port", "Porta interna", "int", 80)],
            self)
        if values and values["interface"] and values["inside_ip"]:
            self.apply_commands(translator.nat_server(
                values["interface"], values["protocol"],
                values["global_ip"], values["global_port"],
                values["inside_ip"], values["inside_port"]))

    def _remove(self):
        rows = self.require_selection("uma ou mais regras")
        if not rows:
            return

        def build(row):
            raw = row["raw"]
            if row["kind"] == "outbound":
                return translator.nat_outbound(row["interface"],
                                               int(raw["acl"]), remove=True)
            # NAT server: "current-interface/8080" ou "1.2.3.4/8080"
            g_ip, _, g_port = raw["global"].rpartition("/")
            i_ip, _, i_port = raw["inside"].rpartition("/")
            if g_ip == "current-interface":
                g_ip = ""
            return translator.nat_server(
                row["interface"], raw["protocol"] or "tcp",
                g_ip, int(g_port or 0), i_ip, int(i_port or 0), remove=True)

        self.apply_for_each(rows, build)
