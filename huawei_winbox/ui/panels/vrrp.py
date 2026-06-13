"""Painel VRRP — grupos de redundância de primeiro salto."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class VrrpPanel(BasePanel):
    title = "VRRP"
    columns = ["Interface", "VRID", "Estado", "IP Virtual",
               "Prioridade", "Mestre atual"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Novo grupo...", self._create),
            ("Prioridade...", self._priority),
            ("Preempt...", self._preempt),
            ("Autenticação...", self._auth),
            ("Track interface...", self._track),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover grupo")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete)
        layout.addWidget(del_btn)

    def fetch(self):
        return parsers.parse_vrrp(
            self.session.run("display vrrp brief"))

    def row_values(self, row):
        return [row["interface"], row["vrid"], row["state"],
                row["virtual_ip"], row["priority"], row["master"]]

    def _create(self):
        values = SimpleForm.ask(
            "Novo grupo VRRP",
            [("interface", "Interface", "text", ""),
             ("vrid", "VRID (1-255)", "int", 1),
             ("virtual_ip", "IP virtual", "text", "")],
            self)
        if values and values["interface"] and values["virtual_ip"]:
            self.apply_commands(translator.vrrp_create(
                values["interface"], values["vrid"], values["virtual_ip"]))

    def _priority(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Prioridade VRRP",
            [("interface", "Interface", "text", row["interface"] if row else ""),
             ("vrid", "VRID", "int", int(row["vrid"]) if row else 1),
             ("priority", "Prioridade (1-254, 100=padrão)", "int", 100)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.vrrp_priority(
                values["interface"], values["vrid"], values["priority"]))

    def _preempt(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Modo Preempt VRRP",
            [("interface", "Interface", "text", row["interface"] if row else ""),
             ("vrid", "VRID", "int", int(row["vrid"]) if row else 1),
             ("enable", "Habilitar preempt", "bool", True),
             ("delay", "Atraso (segundos)", "int", 0)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.vrrp_preempt(
                values["interface"], values["vrid"],
                values["enable"], values["delay"]))

    def _auth(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Autenticação VRRP",
            [("interface", "Interface", "text", row["interface"] if row else ""),
             ("vrid", "VRID", "int", int(row["vrid"]) if row else 1),
             ("key", "Chave (vazio = remover)", "text", "")],
            self)
        if values and values["interface"]:
            remove = not values["key"]
            self.apply_commands(translator.vrrp_auth(
                values["interface"], values["vrid"],
                values["key"], remove=remove))

    def _track(self):
        row = self.selected_row()
        values = SimpleForm.ask(
            "Track de interface VRRP",
            [("interface", "Interface VRRP", "text", row["interface"] if row else ""),
             ("vrid", "VRID", "int", int(row["vrid"]) if row else 1),
             ("track_iface", "Interface monitorada", "text", ""),
             ("reduced", "Redução de prioridade", "int", 10),
             ("remove", "Remover track", "bool", False)],
            self)
        if values and values["interface"] and values["track_iface"]:
            self.apply_commands(translator.vrrp_track_interface(
                values["interface"], values["vrid"],
                values["track_iface"], values["reduced"],
                remove=values["remove"]))

    def _delete(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um grupo.")
            return
        self.apply_commands(translator.vrrp_delete(
            row["interface"], int(row["vrid"])))
