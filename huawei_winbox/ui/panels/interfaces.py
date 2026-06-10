"""Painel de Interfaces — equivalente à janela *Interfaces* do WinBox."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class InterfacesPanel(BasePanel):
    title = "Interfaces"
    columns = ["Interface", "Físico", "Protocolo", "Util. In",
               "Util. Out", "Descrição"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar", lambda: self._toggle(True)),
            ("Desabilitar", lambda: self._toggle(False)),
            ("Descrição...", self._edit_description),
            ("Endereço IP...", self._add_ip),
            ("MTU...", self._set_mtu),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_interface_brief(
            self.session.run("display interface brief"))

    def row_values(self, row):
        return [row["name"], row["phy"], row["protocol"],
                row["in_util"], row["out_util"], row["description"]]

    # ----------------------------------------------------------------- #
    def _need_selection(self) -> dict | None:
        row = self.selected_row()
        if not row:
            QMessageBox.information(
                self, self.title, "Selecione uma interface na tabela.")
        return row

    def _toggle(self, enable: bool):
        row = self._need_selection()
        if row:
            self.apply_commands(
                translator.interface_shutdown(row["name"], enable))

    def _edit_description(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Descrição — {row['name']}",
            [("description", "Descrição", "text", row["description"])],
            self)
        if values is not None:
            self.apply_commands(translator.interface_description(
                row["name"], values["description"]))

    def _add_ip(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Endereço IP — {row['name']}",
            [("address", "Endereço IP", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("remove", "Remover este endereço", "bool", False)],
            self)
        if values and values["address"] and values["mask"]:
            self.apply_commands(translator.interface_ip(
                row["name"], values["address"], values["mask"],
                remove=values["remove"]))

    def _set_mtu(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"MTU — {row['name']}",
            [("mtu", "MTU", "int", 1500)], self)
        if values and values["mtu"]:
            self.apply_commands(
                translator.interface_mtu(row["name"], values["mtu"]))
