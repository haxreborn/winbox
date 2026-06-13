"""Painel QoS — classificadores, comportamentos e políticas de tráfego."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class QosPanel(BasePanel):
    title = "QoS"
    columns = ["Tipo", "Nome", "Detalhe", "Interface/Direção"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Classificador...", self._add_classifier),
            ("Comportamento...", self._add_behavior),
            ("Política...", self._add_policy),
            ("Aplicar na iface...", self._apply_iface),
            ("Remover da iface...", self._remove_iface),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_qos_policies(
            self.session.run("display traffic policy applied-record"))

    def row_values(self, row):
        return [row["type"], row["name"],
                row.get("detail", ""), row.get("applied", "")]

    def _add_classifier(self):
        values = SimpleForm.ask(
            "Classificador de tráfego",
            [("name", "Nome", "text", ""),
             ("dscp", "DSCP (ex.: ef af11, vazio=qualquer)", "text", ""),
             ("acl", "ACL (0 = não usar)", "int", 0)],
            self)
        if values and values["name"]:
            self.apply_commands(translator.qos_traffic_classifier(
                values["name"], values["dscp"], values["acl"]))

    def _add_behavior(self):
        values = SimpleForm.ask(
            "Comportamento de tráfego",
            [("name", "Nome", "text", ""),
             ("action", "Ação", "choice:permit|deny|remark", "permit"),
             ("queue", "Fila (ex.: be ef af1, vazio=não definir)", "text", ""),
             ("dscp_mark", "Remarcar DSCP (vazio=não)", "text", ""),
             ("rate_kbps", "Limitar taxa Kbps (0=sem limite)", "int", 0)],
            self)
        if values and values["name"]:
            self.apply_commands(translator.qos_traffic_behavior(
                values["name"], values["action"], values["queue"],
                values["dscp_mark"], values["rate_kbps"]))

    def _add_policy(self):
        values = SimpleForm.ask(
            "Política de tráfego",
            [("policy_name", "Nome da política", "text", ""),
             ("classifier", "Classificador", "text", ""),
             ("behavior", "Comportamento", "text", "")],
            self)
        if values and values["policy_name"] and values["classifier"]:
            self.apply_commands(translator.qos_traffic_policy(
                values["policy_name"], values["classifier"],
                values["behavior"]))

    def _apply_iface(self):
        values = SimpleForm.ask(
            "Aplicar política na interface",
            [("interface", "Interface", "text", ""),
             ("policy_name", "Nome da política", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound")],
            self)
        if values and values["interface"] and values["policy_name"]:
            self.apply_commands(translator.qos_policy_apply(
                values["interface"], values["policy_name"],
                values["direction"]))

    def _remove_iface(self):
        values = SimpleForm.ask(
            "Remover política da interface",
            [("interface", "Interface", "text", ""),
             ("policy_name", "Nome da política", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound")],
            self)
        if values and values["interface"] and values["policy_name"]:
            self.apply_commands(translator.qos_policy_apply(
                values["interface"], values["policy_name"],
                values["direction"], remove=True))

    def _remove(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um item.")
            return
        t = row["type"]
        if t == "Classificador":
            self.apply_commands(
                translator.qos_traffic_classifier(row["name"], remove=True))
        elif t == "Comportamento":
            self.apply_commands(
                translator.qos_traffic_behavior(row["name"], remove=True))
        elif t == "Política":
            self.apply_commands(
                translator.qos_traffic_policy(row["name"], "", "",
                                              remove_policy=True))
