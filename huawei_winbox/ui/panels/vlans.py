"""Painel de VLANs (criação, remoção e modo de porta access/trunk)."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class VlansPanel(BasePanel):
    title = "VLANs"
    columns = ["VLAN ID", "Tipo", "Descrição"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Criar VLAN...", self._create),
            ("Criar em lote...", self._batch_create),
            ("Porta Access...", self._port_access),
            ("Porta Trunk...", self._port_trunk),
            ("Trunk PVID...", self._trunk_pvid),
            ("Porta Hybrid...", self._port_hybrid),
            ("VLAN de voz...", self._voice_vlan),
            ("VLANif (SVI)...", self._vlanif),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete)
        layout.addWidget(del_btn)
        del_batch_btn = QPushButton("Remover em lote...")
        del_batch_btn.setObjectName("dangerBtn")
        del_batch_btn.clicked.connect(self._batch_delete)
        layout.addWidget(del_batch_btn)

    def fetch(self):
        return parsers.parse_vlans(self.session.run("display vlan"))

    def row_values(self, row):
        return [row["id"], row["type"], row["description"]]

    def _create(self):
        values = SimpleForm.ask(
            "Nova VLAN",
            [("vlan_id", "VLAN ID (1-4094)", "int", 10),
             ("name", "Nome (opcional)", "text", "")],
            self)
        if values:
            self.apply_commands(
                translator.vlan_create(values["vlan_id"], values["name"]))

    def _batch_create(self):
        values = SimpleForm.ask(
            "Criar VLANs em lote",
            [("vlan_range",
              "Intervalo de VLANs (ex.: 10 20 30 ou 100 to 200)",
              "text", "")],
            self)
        if values and values["vlan_range"]:
            self.apply_commands(
                translator.vlan_batch_create(values["vlan_range"]))

    def _delete(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione uma VLAN.")
            return
        self.apply_commands(translator.vlan_delete(int(row["id"])))

    def _batch_delete(self):
        values = SimpleForm.ask(
            "Remover VLANs em lote",
            [("vlan_range",
              "Intervalo de VLANs (ex.: 10 20 30 ou 100 to 200)",
              "text", "")],
            self)
        if values and values["vlan_range"]:
            self.apply_commands(
                translator.vlan_batch_delete(values["vlan_range"]))

    def _port_access(self):
        values = SimpleForm.ask(
            "Porta em modo access",
            [("interface", "Interface", "text", ""),
             ("vlan_id", "VLAN ID", "int", 10)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.port_access(
                values["interface"], values["vlan_id"]))

    def _port_trunk(self):
        values = SimpleForm.ask(
            "Porta em modo trunk",
            [("interface", "Interface", "text", ""),
             ("vlans",
              "VLANs permitidas (ex.: 10 20 30 ou 10 to 20)",
              "text", "")],
            self)
        if values and values["interface"] and values["vlans"]:
            self.apply_commands(translator.port_trunk(
                values["interface"], values["vlans"]))

    def _trunk_pvid(self):
        values = SimpleForm.ask(
            "PVID de Trunk (VLAN nativa)",
            [("interface", "Interface trunk", "text", ""),
             ("pvid", "PVID (VLAN nativa)", "int", 1)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.port_trunk_pvid(
                values["interface"], values["pvid"]))

    def _port_hybrid(self):
        values = SimpleForm.ask(
            "Porta em modo hybrid",
            [("interface", "Interface", "text", ""),
             ("tagged_vlans",
              "VLANs com tag (ex.: 10 20 ou 10 to 20)",
              "text", ""),
             ("untagged_vlans",
              "VLANs sem tag (ex.: 30 40)",
              "text", ""),
             ("pvid", "PVID (0 = não alterar)", "int", 0)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.port_hybrid(
                values["interface"],
                values["tagged_vlans"],
                values["untagged_vlans"],
                pvid=values["pvid"] or None))

    def _voice_vlan(self):
        values = SimpleForm.ask(
            "VLAN de Voz",
            [("interface", "Interface", "text", ""),
             ("vlan_id", "VLAN ID de voz", "int", 100),
             ("mode",
              "Modo",
              "choice:auto|manual",
              "auto"),
             ("remove", "Remover VLAN de voz", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.voice_vlan(
                values["interface"], values["vlan_id"],
                values["mode"], remove=values["remove"]))

    def _vlanif(self):
        values = SimpleForm.ask(
            "Interface VLAN (SVI / Vlanif)",
            [("vlan_id", "VLAN ID", "int", 10),
             ("ip_address", "Endereço IP (opcional)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("description", "Descrição (opcional)", "text", ""),
             ("remove", "Remover Vlanif", "bool", False)],
            self)
        if not values:
            return
        vlan_id = values["vlan_id"]
        iface = f"Vlanif{vlan_id}"
        if values["remove"]:
            from ...core.translator import interface_shutdown
            self.apply_commands([
                f"undo interface {iface}",
            ])
            return
        cmds: list[str] = [f"interface {iface}"]
        if values["description"]:
            cmds.append(f"description {values['description']}")
        if values["ip_address"] and values["mask"]:
            mask = values["mask"]
            if mask.isdigit():
                cmds.append(f"ip address {values['ip_address']} {mask}")
            else:
                cmds.append(f"ip address {values['ip_address']} {mask}")
        cmds.append("undo shutdown")
        self.apply_commands(cmds)
