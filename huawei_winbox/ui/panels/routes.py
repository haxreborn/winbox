"""Painel de Rotas — tabela de roteamento e rotas estáticas.

A tabela BGP completa de um roteador de borda pode ter centenas de
milhares de rotas — pedir "display ip routing-table" sem filtro trava
qualquer aplicação. Por isso o painel consulta por protocolo (o padrão
"Sem BGP" cobre direct/static/OSPF/IS-IS/RIP), oferece busca de prefixo
(longest match, ideal para conferir uma rota BGP específica) e limita a
exibição a MAX_ROWS linhas.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QPushButton, QMessageBox, QComboBox, QLineEdit, QLabel,
)

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel

# Protocolos pequenos consultados no modo "Sem BGP".
_NON_BGP_PROTOCOLS = ("direct", "static", "ospf", "isis", "rip")


class RoutesPanel(BasePanel):
    title = "Rotas"
    columns = ["Destino", "Protocolo", "Pref", "Custo",
               "Próximo salto", "Interface"]
    MAX_ROWS = 2000

    def build_toolbar(self, layout):
        layout.addWidget(QLabel("Protocolo:"))
        self.proto_combo = QComboBox()
        self.proto_combo.addItems(
            ["Sem BGP", "BGP", "Static", "OSPF", "Direct", "Todas"])
        self.proto_combo.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.proto_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Buscar prefixo (ex.: 8.8.8.8)")
        self.search_edit.setMaximumWidth(180)
        self.search_edit.returnPressed.connect(self.refresh)
        layout.addWidget(self.search_edit)
        search_btn = QPushButton("Buscar")
        search_btn.clicked.connect(self.refresh)
        layout.addWidget(search_btn)

        add_btn = QPushButton("Rota estática...")
        add_btn.clicked.connect(self._add_static)
        layout.addWidget(add_btn)
        del_btn = QPushButton("Remover estática")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove_static)
        layout.addWidget(del_btn)

    def refresh(self, *args, **kwargs):
        # Captura o estado dos widgets AQUI (thread da UI) — fetch roda
        # no worker e não pode tocar em widgets Qt. Os argumentos seguem
        # intactos: é por eles que a BasePanel sabe se é clique ou
        # atualização automática.
        self._mode = self.proto_combo.currentText()
        self._query = self.search_edit.text().strip()
        super().refresh(*args, **kwargs)

    def fetch(self):
        self._truncated = False
        # Busca de prefixo: longest match, resposta sempre pequena —
        # é o jeito certo de conferir uma rota específica da tabela BGP.
        if self._query:
            text = self.session.run(
                f"display ip routing-table {self._query}", timeout=30.0)
            return parsers.parse_routing_table(text)

        mode = self._mode
        if mode == "Sem BGP":
            rows = []
            for proto in _NON_BGP_PROTOCOLS:
                try:
                    rows += parsers.parse_routing_table(self.session.run(
                        f"display ip routing-table protocol {proto}",
                        timeout=30.0))
                except Exception:
                    pass  # protocolo não configurado/não suportado
        elif mode == "Todas":
            rows = parsers.parse_routing_table(self.session.run(
                "display ip routing-table", timeout=300.0))
        else:
            rows = parsers.parse_routing_table(self.session.run(
                f"display ip routing-table protocol {mode.lower()}",
                timeout=300.0))

        if len(rows) > self.MAX_ROWS:
            self._truncated = len(rows)
            rows = rows[:self.MAX_ROWS]
        return rows

    def _on_rows(self, rows):
        # Suspende o repaint durante a carga — com milhares de linhas a
        # inserção célula a célula fica muito mais rápida assim.
        self.table.setUpdatesEnabled(False)
        try:
            super()._on_rows(rows)
        finally:
            self.table.setUpdatesEnabled(True)
        if self._truncated:
            self._set_busy(
                False,
                f"Mostrando {self.MAX_ROWS} de {self._truncated} rotas — "
                "use a busca de prefixo para consultar uma rota específica")

    def row_values(self, row):
        return [row["destination"], row["proto"], row["pre"],
                row["cost"], row["nexthop"], row["interface"]]

    def _add_static(self):
        values = SimpleForm.ask(
            "Nova rota estática",
            [("destination", "Rede de destino (ex.: 10.0.0.0)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("next_hop", "Próximo salto", "text", ""),
             ("preference", "Preferência (0 = padrão 60)", "int", 0),
             ("description", "Descrição (opcional)", "text", "")],
            self)
        if values and values["destination"] and values["next_hop"]:
            self.apply_commands(translator.static_route(
                values["destination"], values["mask"], values["next_hop"],
                preference=values["preference"] or None,
                description=values["description"]))

    def _remove_static(self):
        row = self.selected_row()
        if not row or row["proto"].lower() != "static":
            QMessageBox.information(
                self, self.title,
                "Selecione uma rota do tipo Static para remover.")
            return
        destination, prefix = row["destination"].split("/", 1)
        self.apply_commands(translator.static_route(
            destination, prefix, row["nexthop"], remove=True))
