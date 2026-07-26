"""Painel Vizinhos — descoberta LLDP em tabela e em diagrama.

O *Neighbors* do WinBox é uma lista. Uma lista responde *quem* está
conectado; o desenho responde **como este equipamento está no meio da
rede** — que é o que se quer saber ao abrir uma caixa pela primeira vez ou
quando alguém pergunta "esse switch está pendurado onde?".

No diagrama, a espessura de cada ligação acompanha o tráfego real da porta
local, então o uplink de verdade salta aos olhos no meio dos links de
acesso. As portas fora do ar aparecem tracejadas. Clicar numa ligação abre
o gráfico daquela porta.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QPushButton, QTabWidget, QVBoxLayout, QWidget, QLabel, QHBoxLayout,
    QScrollArea,
)

from ...core import parsers, telemetry, translator
from ...core import history as history_mod
from ..widgets.topology import TopologyWidget, Neighbor
from .base import BasePanel


class LldpPanel(BasePanel):
    title = "Vizinhos (LLDP)"
    columns = ["Interface local", "Equipamento vizinho",
               "Interface do vizinho", "Endereço de gerência",
               "Descrição da porta", "Expira (s)"]

    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        self.engine = telemetry.for_session(session)
        self.history = history_mod.for_session(session)
        self._neighbors: list[dict] = []
        super().__init__(session, parent)

    # ------------------------------------------------------------------ #
    def build_toolbar(self, layout):
        on_btn = QPushButton("Habilitar LLDP")
        on_btn.clicked.connect(
            lambda: self.apply_commands(translator.lldp_enable(True)))
        layout.addWidget(on_btn)
        off_btn = QPushButton("Desabilitar LLDP")
        off_btn.setObjectName("dangerBtn")
        off_btn.clicked.connect(
            lambda: self.apply_commands(translator.lldp_enable(False)))
        layout.addWidget(off_btn)

    def fetch(self):
        # O verboso traz endereço de gerência e descrição da porta remota,
        # que é o que dá substância ao diagrama. Se a plataforma não o
        # tiver, o brief ainda serve para nome e porta.
        detail = parsers.parse_lldp_neighbors_detail(
            self.session.run("display lldp neighbor", timeout=45.0))
        if detail:
            return detail
        rows = parsers.parse_lldp_neighbors(
            self.session.run("display lldp neighbor brief", timeout=30.0))
        for row in rows:
            row.setdefault("management_ip", "")
            row.setdefault("port_description", "")
        return rows

    def row_values(self, row):
        return [row.get("local", ""), row.get("neighbor_dev", ""),
                row.get("neighbor_intf", ""), row.get("management_ip", ""),
                row.get("port_description", ""), row.get("exptime", "")]

    def _on_rows(self, rows):
        super()._on_rows(rows)
        if rows is not None:
            self._neighbors = rows
            self._draw_topology()

    # ------------------------------------------------------------------ #
    # A BasePanel monta a tabela; aqui ela ganha uma aba com o desenho.
    # ------------------------------------------------------------------ #
    def _install_topology(self) -> None:
        layout = self.layout()
        index = layout.indexOf(self.table)
        layout.removeWidget(self.table)

        self._tabs = QTabWidget()
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table)
        self._tabs.addTab(table_page, "Lista")

        diagram_page = QWidget()
        diagram_layout = QVBoxLayout(diagram_page)
        diagram_layout.setContentsMargins(0, 0, 0, 0)
        diagram_layout.setSpacing(2)
        self.topology = TopologyWidget()
        self.topology.portClicked.connect(self._open_port_graph)
        # Com muitos vizinhos o desenho pede mais espaço do que a janela
        # tem; rolar é melhor que espremer as caixas umas nas outras.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.topology)
        scroll.setFrameShape(QScrollArea.NoFrame)
        diagram_layout.addWidget(scroll, 1)
        legend = QHBoxLayout()
        note = QLabel(
            "A espessura da linha acompanha o tráfego da porta local · "
            "tracejado = porta fora do ar · clique numa ligação para abrir "
            "o gráfico dela")
        note.setObjectName("itemCount")
        note.setWordWrap(True)
        legend.addWidget(note)
        diagram_layout.addLayout(legend)
        self._tabs.addTab(diagram_page, "Diagrama")

        layout.insertWidget(index, self._tabs, 1)

    def _draw_topology(self) -> None:
        if not hasattr(self, "topology"):
            return
        snapshot = self.engine.snapshot()
        rates = snapshot.get("rates") or {}
        meta = self.engine.interface_meta()

        # Um mesmo vizinho pode aparecer em várias portas (agregação); o
        # diagrama mostra uma caixa por equipamento, com todas as ligações.
        grouped: dict[str, Neighbor] = {}
        for row in self._neighbors:
            device = row.get("neighbor_dev") or "(sem nome)"
            entry = grouped.get(device)
            if entry is None:
                entry = Neighbor(device, row.get("management_ip", ""))
                grouped[device] = entry
            elif not entry.management_ip:
                entry.management_ip = row.get("management_ip", "")
            local = row.get("local", "")
            pair = rates.get(local) or (0.0, 0.0)
            state = (meta.get(local) or {}).get("oper", "")
            entry.links.append((local, row.get("neighbor_intf", ""),
                                pair[0], pair[1],
                                state != "down"))

        self.topology.set_center(
            self.session.hostname or self.session.host,
            self.session.host)
        self.topology.set_neighbors(
            sorted(grouped.values(), key=lambda n: (-n.traffic, n.device)))

    def _open_port_graph(self, port: str) -> None:
        if port:
            self.open_panel_requested.emit("Tráfego (gráfico)", [port])

    # ------------------------------------------------------------------ #
    def showEvent(self, event):
        # A aba só pode ser montada depois que a BasePanel terminou de
        # construir o layout — daí ser feito na primeira exibição.
        if not hasattr(self, "_tabs"):
            self._install_topology()
            self._draw_topology()
        super().showEvent(event)

    def focus_payload(self, payload) -> None:
        """Mostra o diagrama quando outro painel manda abrir os vizinhos."""
        if hasattr(self, "_tabs"):
            self._tabs.setCurrentIndex(1)
