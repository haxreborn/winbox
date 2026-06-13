"""Painel DHCP — pools de endereços, relay e ativação por interface."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class DhcpPanel(BasePanel):
    title = "Servidor DHCP"
    columns = ["Pool", "Rede", "Gateway", "Em uso", "Livres"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Novo pool...", self._add_pool),
            ("Ativar na interface...", self._iface),
            ("Excluir endereços...", self._excluded),
            ("IP estático...", self._static_bind),
            ("Relay DHCP...", self._relay),
            ("Snooping...", self._snooping),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover pool")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete_pool)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_ip_pools(self.session.run("display ip pool"))

    def row_values(self, row):
        return [row["name"], row["network"], row["gateway"],
                row["used"], row["idle"]]

    def _add_pool(self):
        values = SimpleForm.ask(
            "Novo pool DHCP",
            [("name", "Nome do pool", "text", ""),
             ("network", "Rede (ex.: 192.168.10.0)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("gateway", "Gateway (opcional)", "text", ""),
             ("dns", "Servidor DNS (opcional)", "text", ""),
             ("lease_days", "Duração do lease (dias)", "int", 1)],
            self)
        if values and values["name"] and values["network"]:
            self.apply_commands(translator.dhcp_pool(
                values["name"], values["network"], values["mask"],
                values["gateway"], values["dns"], values["lease_days"]))

    def _delete_pool(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um pool.")
            return
        self.apply_commands(translator.dhcp_pool_delete(row["name"]))

    def _iface(self):
        values = SimpleForm.ask(
            "DHCP na interface",
            [("interface", "Interface (ex.: Vlanif10)", "text", ""),
             ("remove", "Desativar (undo)", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.dhcp_interface(
                values["interface"], remove=values["remove"]))

    def _excluded(self):
        values = SimpleForm.ask(
            "Excluir endereços do pool",
            [("pool_name", "Nome do pool", "text", ""),
             ("start_ip", "IP inicial excluído", "text", ""),
             ("end_ip", "IP final excluído (opcional)", "text", ""),
             ("remove", "Remover exclusão", "bool", False)],
            self)
        if not values or not values["pool_name"] or not values["start_ip"]:
            return
        cmds: list[str] = [f"ip pool {values['pool_name']}"]
        if values["remove"]:
            if values["end_ip"]:
                cmds.append(
                    f"undo excluded-ip-address {values['start_ip']} "
                    f"{values['end_ip']}")
            else:
                cmds.append(
                    f"undo excluded-ip-address {values['start_ip']}")
        else:
            if values["end_ip"]:
                cmds.append(
                    f"excluded-ip-address {values['start_ip']} "
                    f"{values['end_ip']}")
            else:
                cmds.append(
                    f"excluded-ip-address {values['start_ip']}")
        self.apply_commands(cmds)

    def _static_bind(self):
        values = SimpleForm.ask(
            "Binding IP estático (reserva DHCP)",
            [("pool_name", "Nome do pool", "text", ""),
             ("ip", "Endereço IP reservado", "text", ""),
             ("mac", "Endereço MAC (ex.: aabb-ccdd-eeff)", "text", ""),
             ("remove", "Remover binding", "bool", False)],
            self)
        if not values or not values["pool_name"] or not values["ip"]:
            return
        cmds: list[str] = [f"ip pool {values['pool_name']}"]
        if values["remove"]:
            cmds.append(f"undo static-bind ip-address {values['ip']}")
        else:
            mac = values["mac"]
            if mac:
                cmds.append(
                    f"static-bind ip-address {values['ip']} "
                    f"mac-address {mac}")
            else:
                cmds.append(f"static-bind ip-address {values['ip']}")
        self.apply_commands(cmds)

    def _relay(self):
        values = SimpleForm.ask(
            "DHCP Relay",
            [("interface", "Interface do cliente (ex.: Vlanif10)", "text", ""),
             ("server_ip", "IP do servidor DHCP", "text", ""),
             ("gateway_ip",
              "IP da interface (gateway p/ relay)",
              "text", ""),
             ("remove", "Remover relay", "bool", False)],
            self)
        if values and values["interface"] and values["server_ip"]:
            self.apply_commands(translator.dhcp_relay_server(
                values["interface"], values["server_ip"],
                gateway_ip=values["gateway_ip"] or None,
                remove=values["remove"]))

    def _snooping(self):
        values = SimpleForm.ask(
            "DHCP Snooping",
            [("action",
              "Ação",
              "choice:Habilitar globalmente|Habilitar na VLAN|Habilitar na interface|Desabilitar globalmente",
              "Habilitar globalmente"),
             ("vlan_id", "VLAN ID (para VLAN)", "int", 10),
             ("interface", "Interface (para interface)", "text", ""),
             ("trusted",
              "Porta confiável (trusted)",
              "bool", False)],
            self)
        if not values:
            return
        action = values["action"]
        if action == "Habilitar globalmente":
            self.apply_commands(translator.dhcp_snooping_enable(True))
        elif action == "Desabilitar globalmente":
            self.apply_commands(translator.dhcp_snooping_enable(False))
        elif action == "Habilitar na VLAN":
            self.apply_commands(translator.dhcp_snooping_vlan(
                values["vlan_id"], True))
        elif action == "Habilitar na interface" and values["interface"]:
            self.apply_commands(translator.dhcp_snooping_interface(
                values["interface"], trusted=values["trusted"]))
