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
            ("Tipo de área...", self._area_type),
            ("Redistribuir...", self._redistribute),
            ("Autenticação...", self._auth),
            ("Rota padrão...", self._default_route),
            ("Custo interface...", self._iface_cost),
            ("OSPFv3 (IPv6)...", self._ospfv3),
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

    def _area_type(self):
        values = SimpleForm.ask(
            "Tipo de Área OSPF",
            [("process_id", "ID do processo", "int", 1),
             ("area", "Área", "text", "1"),
             ("area_type",
              "Tipo",
              "choice:stub|nssa|normal",
              "stub"),
             ("no_summary", "Sem resumo (totally stub/nssa)", "bool", False),
             ("remove", "Reverter para normal", "bool", False)],
            self)
        if values:
            self.apply_commands(translator.ospf_area_type(
                values["process_id"], values["area"], values["area_type"],
                no_summary=values["no_summary"], remove=values["remove"]))

    def _redistribute(self):
        values = SimpleForm.ask(
            "Redistribuir rotas no OSPF",
            [("process_id", "ID do processo", "int", 1),
             ("protocol",
              "Protocolo de origem",
              "choice:static|bgp|rip|isis|direct",
              "static"),
             ("cost", "Custo (0 = padrão)", "int", 0),
             ("type", "Tipo (1 ou 2)", "choice:1|2", "2"),
             ("remove", "Remover redistribuição", "bool", False)],
            self)
        if values:
            self.apply_commands(translator.ospf_redistribute(
                values["process_id"], values["protocol"],
                cost=values["cost"] or None,
                route_type=int(values["type"]),
                remove=values["remove"]))

    def _auth(self):
        values = SimpleForm.ask(
            "Autenticação de Área OSPF",
            [("process_id", "ID do processo", "int", 1),
             ("area", "Área", "text", "0"),
             ("auth_type",
              "Tipo de autenticação",
              "choice:simple|md5|keychain",
              "md5"),
             ("key_id", "Key ID (para MD5)", "int", 1),
             ("password", "Senha/chave", "text", ""),
             ("remove", "Remover autenticação", "bool", False)],
            self)
        if values:
            self.apply_commands(translator.ospf_authentication(
                values["process_id"], values["area"], values["auth_type"],
                key_id=values["key_id"], password=values["password"],
                remove=values["remove"]))

    def _default_route(self):
        values = SimpleForm.ask(
            "Rota Padrão no OSPF",
            [("process_id", "ID do processo", "int", 1),
             ("always", "Sempre anunciar (mesmo sem rota padrão)", "bool", True),
             ("cost", "Custo (0 = padrão)", "int", 0),
             ("type", "Tipo (1 ou 2)", "choice:1|2", "2"),
             ("remove", "Remover rota padrão", "bool", False)],
            self)
        if values:
            self.apply_commands(translator.ospf_default_route(
                values["process_id"], always=values["always"],
                cost=values["cost"] or None,
                route_type=int(values["type"]),
                remove=values["remove"]))

    def _iface_cost(self):
        values = SimpleForm.ask(
            "Custo de Interface OSPF",
            [("interface", "Interface", "text", ""),
             ("process_id", "ID do processo", "int", 1),
             ("cost", "Custo (1-65535)", "int", 10),
             ("remove", "Restaurar custo automático", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.ospf_interface_cost(
                values["interface"], values["process_id"],
                values["cost"], remove=values["remove"]))

    def _ospfv3(self):
        values = SimpleForm.ask(
            "OSPFv3 (IPv6)",
            [("process_id", "ID do processo OSPFv3", "int", 1),
             ("router_id", "Router ID (obrigatório para OSPFv3)", "text", ""),
             ("interface", "Interface (opcional, para habilitar OSPFv3)", "text", ""),
             ("area", "Área da interface", "text", "0"),
             ("remove", "Remover OSPFv3 da interface", "bool", False)],
            self)
        if not values:
            return
        cmds = translator.ospfv3_enable(values["process_id"], values["router_id"])
        if values["interface"]:
            cmds += translator.ospfv3_interface(
                values["interface"], values["process_id"],
                values["area"], remove=values["remove"])
        self.apply_commands(cmds)
