"""Painel Sistema — identidade, recursos e gravação da configuração."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class SystemPanel(BasePanel):
    title = "Sistema"
    columns = ["Item", "Valor"]

    def build_toolbar(self, layout):
        name_btn = QPushButton("Renomear equipamento...")
        name_btn.clicked.connect(self._rename)
        layout.addWidget(name_btn)
        save_btn = QPushButton("Salvar configuração")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

    def fetch(self):
        version = parsers.parse_version(self.session.run("display version"))
        cpu = parsers.parse_cpu_usage(self.session.run("display cpu-usage"))
        mem = parsers.parse_memory_usage(self.session.run("display memory-usage"))
        return [
            {"item": "Nome do equipamento", "valor": self.session.hostname},
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
        # "save" roda fora da visão de sistema.
        self.apply_commands(translator.save_config(),
                            refresh_after=False, system_view=False)
