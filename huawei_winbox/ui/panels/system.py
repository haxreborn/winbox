"""Painel Sistema — identidade, recursos e configurações globais."""

from __future__ import annotations

import re

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class SystemPanel(BasePanel):
    title = "Sistema"
    columns = ["Item", "Valor"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Renomear...", self._rename),
            ("Salvar config", self._save),
            ("DNS...", self._dns),
            ("NTP...", self._ntp),
            ("Syslog...", self._syslog),
            ("RADIUS...", self._radius),
            ("TACACS+...", self._tacacs),
            ("SSH/Telnet...", self._ssh_telnet),
            ("ACL de VTY...", self._vty_acl),
            ("BFD global...", self._bfd),
            ("VPN/VRF...", self._vpn_instance),
            ("Route-Policy...", self._route_policy),
            ("Prefix List...", self._prefix_list),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        version_text = self.session.run("display version")
        cpu_text = self.session.run("display cpu-usage")
        mem_text = self.session.run("display memory-usage")
        sysname_text = self.session.run("display sysname")
        sysname = ""
        m = re.search(r"[Ss]ysname\s+(?:is\s+)?(\S+)", sysname_text)
        if m:
            sysname = m.group(1)
            self.session.hostname = sysname
        if not sysname:
            sysname = self.session.hostname

        version = parsers.parse_version(version_text)
        cpu = parsers.parse_cpu_usage(cpu_text)
        mem = parsers.parse_memory_usage(mem_text)
        return [
            {"item": "Nome do equipamento (sysname)", "valor": sysname},
            {"item": "Produto", "valor": version["product"] or "—"},
            {"item": "Software (VRP)", "valor": version["software"] or "—"},
            {"item": "Tempo ligado", "valor": version["uptime"] or "—"},
            {"item": "Uso de CPU", "valor": cpu},
            {"item": "Uso de memória", "valor": mem},
        ]

    def row_values(self, row):
        return [row["item"], row["valor"]]

    def _rename(self):
        values = SimpleForm.ask(
            "Renomear equipamento",
            [("name", "Novo nome (sysname)", "text", self.session.hostname)],
            self)
        if values and values["name"]:
            self.apply_commands(translator.set_sysname(values["name"]))

    def _save(self):
        self.apply_commands(translator.save_config(),
                            refresh_after=False, system_view=False)

    def _dns(self):
        values = SimpleForm.ask(
            "Configurar DNS",
            [("action", "Ação", "choice:Habilitar|Desabilitar|Servidor|Domínio", "Servidor"),
             ("server_ip", "Servidor DNS (ex.: 8.8.8.8)", "text", ""),
             ("domain", "Domínio de busca (opcional)", "text", ""),
             ("remove", "Remover esta configuração", "bool", False)],
            self)
        if not values:
            return
        cmds: list[str] = []
        action = values["action"]
        if action == "Habilitar":
            cmds = translator.dns_enable(True)
        elif action == "Desabilitar":
            cmds = translator.dns_enable(False)
        elif action == "Servidor" and values["server_ip"]:
            cmds = translator.dns_server(
                values["server_ip"], remove=values["remove"])
        elif action == "Domínio" and values["domain"]:
            cmds = translator.dns_domain(
                values["domain"], remove=values["remove"])
        if cmds:
            self.apply_commands(cmds)

    def _ntp(self):
        values = SimpleForm.ask(
            "Configurar NTP",
            [("server_ip", "Servidor NTP (ex.: 200.160.7.186)", "text", ""),
             ("timezone", "Fuso horário (ex.: BRT)", "text", ""),
             ("offset_sign", "Sinal do offset", "choice:+|-", "-"),
             ("hours", "Horas do offset (0-12)", "int", 3),
             ("minutes", "Minutos do offset (0-59)", "int", 0)],
            self)
        if not values:
            return
        cmds: list[str] = []
        if values["timezone"]:
            cmds += translator.ntp_timezone(
                values["timezone"], values["offset_sign"],
                values["hours"], values["minutes"])
        if values["server_ip"]:
            cmds += translator.ntp_sync(values["server_ip"])
        if cmds:
            self.apply_commands(cmds)

    def _syslog(self):
        values = SimpleForm.ask(
            "Configurar Syslog",
            [("server_ip", "Servidor Syslog", "text", ""),
             ("port", "Porta UDP", "int", 514),
             ("level",
              "Nível mínimo",
              "choice:emergencies|alerts|critical|errors|warnings|notifications|informational|debugging",
              "warnings"),
             ("timestamp",
              "Formato do timestamp",
              "choice:date|boot|none",
              "date"),
             ("remove", "Remover servidor Syslog", "bool", False)],
            self)
        if not values:
            return
        cmds: list[str] = []
        if values["server_ip"]:
            cmds += translator.syslog_server(
                values["server_ip"], values["port"], remove=values["remove"])
        if not values["remove"]:
            cmds += translator.syslog_level(values["level"])
            cmds += translator.syslog_timestamp(values["timestamp"])
        if cmds:
            self.apply_commands(cmds)

    def _radius(self):
        values = SimpleForm.ask(
            "Servidor RADIUS",
            [("server_ip", "IP do servidor RADIUS", "text", ""),
             ("auth_port", "Porta de autenticação", "int", 1812),
             ("acct_port", "Porta de contabilização", "int", 1813),
             ("shared_key", "Chave compartilhada", "text", ""),
             ("group_name", "Nome do grupo RADIUS", "text", "radius_group"),
             ("remove", "Remover servidor", "bool", False)],
            self)
        if values and values["server_ip"] and values["shared_key"]:
            self.apply_commands(translator.radius_server(
                values["server_ip"], values["auth_port"],
                values["acct_port"], values["shared_key"],
                group_name=values["group_name"],
                remove=values["remove"]))

    def _tacacs(self):
        values = SimpleForm.ask(
            "Servidor TACACS+",
            [("server_ip", "IP do servidor TACACS+", "text", ""),
             ("port", "Porta", "int", 49),
             ("shared_key", "Chave compartilhada", "text", ""),
             ("group_name", "Nome do grupo TACACS+", "text", "tacacs_group"),
             ("remove", "Remover servidor", "bool", False)],
            self)
        if values and values["server_ip"] and values["shared_key"]:
            self.apply_commands(translator.tacacs_server(
                values["server_ip"], values["port"], values["shared_key"],
                group_name=values["group_name"],
                remove=values["remove"]))

    def _ssh_telnet(self):
        values = SimpleForm.ask(
            "Serviços SSH / Telnet / FTP",
            [("ssh_version", "Versão SSH", "choice:2|1|1.99", "2"),
             ("telnet", "Telnet", "choice:Habilitar|Desabilitar", "Desabilitar"),
             ("ftp", "Servidor FTP", "choice:Desabilitar|Habilitar", "Desabilitar")],
            self)
        if not values:
            return
        cmds: list[str] = []
        cmds += translator.ssh_version(values["ssh_version"])
        cmds += translator.telnet_enable(values["telnet"] == "Habilitar")
        cmds += translator.ftp_enable(values["ftp"] == "Habilitar")
        self.apply_commands(cmds)

    def _vty_acl(self):
        values = SimpleForm.ask(
            "ACL para acesso VTY (linha de terminal)",
            [("acl_number",
              "Número da ACL (2000-3999)",
              "int", 2000),
             ("remove", "Remover ACL do VTY", "bool", False)],
            self)
        if values:
            self.apply_commands(translator.vty_acl(
                values["acl_number"], remove=values["remove"]))

    def _bfd(self):
        values = SimpleForm.ask(
            "BFD Global",
            [("action",
              "Ação",
              "choice:Habilitar BFD global|Criar sessão BFD",
              "Habilitar BFD global"),
             ("local_disc", "Discriminador local (para sessão)", "int", 1),
             ("peer_ip", "IP do peer (para sessão)", "text", ""),
             ("src_ip", "IP local (para sessão)", "text", ""),
             ("min_tx", "Intervalo TX mínimo (ms)", "int", 1000),
             ("min_rx", "Intervalo RX mínimo (ms)", "int", 1000),
             ("multiplier", "Multiplicador", "int", 3)],
            self)
        if not values:
            return
        if values["action"] == "Habilitar BFD global":
            self.apply_commands(translator.bfd_enable())
        elif values["peer_ip"] and values["src_ip"]:
            self.apply_commands(translator.bfd_session(
                values["local_disc"], values["peer_ip"], values["src_ip"],
                values["min_tx"], values["min_rx"], values["multiplier"]))

    def _vpn_instance(self):
        values = SimpleForm.ask(
            "VPN Instance / VRF",
            [("action",
              "Ação",
              "choice:Criar VRF|Vincular interface|BGP VRF",
              "Criar VRF"),
             ("vrf_name", "Nome do VRF", "text", ""),
             ("rd", "Route Distinguisher (ex.: 65000:100)", "text", ""),
             ("rt_import", "RT Import (ex.: 65000:100)", "text", ""),
             ("rt_export", "RT Export (ex.: 65000:100)", "text", ""),
             ("interface", "Interface (para Vincular ou BGP VRF)", "text", ""),
             ("local_as", "AS local (para BGP VRF)", "int", 0),
             ("remove", "Remover VRF/Binding", "bool", False)],
            self)
        if not values or not values["vrf_name"]:
            return
        action = values["action"]
        if action == "Criar VRF":
            self.apply_commands(translator.vpn_instance_create(
                values["vrf_name"], values["rd"],
                values["rt_import"], values["rt_export"],
                remove=values["remove"]))
        elif action == "Vincular interface" and values["interface"]:
            self.apply_commands(translator.vpn_interface_bind(
                values["interface"], values["vrf_name"],
                remove=values["remove"]))
        elif action == "BGP VRF" and values["local_as"]:
            self.apply_commands(translator.bgp_vpn_instance(
                values["local_as"], values["vrf_name"],
                values["rd"], values["rt_import"], values["rt_export"]))

    def _route_policy(self):
        values = SimpleForm.ask(
            "Route-Policy",
            [("policy_name", "Nome da policy", "text", ""),
             ("node", "Node (número do nó)", "int", 10),
             ("mode", "Modo", "choice:permit|deny", "permit"),
             ("action",
              "Ação do nó",
              "choice:Criar nó|Match Prefix-List|Set Community",
              "Criar nó"),
             ("prefix_list", "Prefix-list (para Match)", "text", ""),
             ("community", "Community (para Set, ex.: 65000:100)", "text", ""),
             ("remove", "Remover nó", "bool", False)],
            self)
        if not values or not values["policy_name"]:
            return
        action = values["action"]
        if action == "Criar nó":
            self.apply_commands(translator.route_policy_node(
                values["policy_name"], values["node"], values["mode"],
                remove=values["remove"]))
        elif action == "Match Prefix-List" and values["prefix_list"]:
            cmds = translator.route_policy_node(
                values["policy_name"], values["node"], values["mode"])
            cmds += translator.route_policy_match_prefix(
                values["policy_name"], values["node"], values["prefix_list"])
            self.apply_commands(cmds)
        elif action == "Set Community" and values["community"]:
            cmds = translator.route_policy_node(
                values["policy_name"], values["node"], values["mode"])
            cmds += translator.route_policy_set_community(
                values["policy_name"], values["node"], values["community"])
            self.apply_commands(cmds)

    def _prefix_list(self):
        values = SimpleForm.ask(
            "IP Prefix List",
            [("name", "Nome da lista", "text", ""),
             ("index", "Índice (número da entrada)", "int", 10),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("prefix", "Prefixo (ex.: 10.0.0.0/8)", "text", ""),
             ("ge", "GE (comprimento mínimo, 0 = não usar)", "int", 0),
             ("le", "LE (comprimento máximo, 0 = não usar)", "int", 0),
             ("remove", "Remover entrada", "bool", False)],
            self)
        if values and values["name"] and values["prefix"]:
            self.apply_commands(translator.ip_prefix_list(
                values["name"], values["index"], values["action"],
                values["prefix"],
                ge=values["ge"] or None,
                le=values["le"] or None,
                remove=values["remove"]))
