"""Painel BGP — vizinhos IPv4 e IPv6, anúncio de redes e configuração."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QMessageBox, QTableWidgetItem

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class BgpPanel(BasePanel):
    title = "BGP"
    columns = ["Vizinho", "AS remoto", "Família",
               "Up/Down", "Estado", "Prefixos"]

    def __init__(self, session, parent=None):
        self.local_as = 0
        super().__init__(session, parent)

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar BGP...", self._enable_bgp),
            ("Novo vizinho...", self._add_peer),
            ("Vizinho IPv6...", self._add_peer_ipv6),
            ("Grupo de peers...", self._peer_group),
            ("Anunciar rede...", self._add_network),
            ("Desabilitar peer", self._disable_peer),
            ("Habilitar peer", self._enable_peer),
            ("Soft Reset...", self._soft_reset),
            ("Route-Policy...", self._route_policy),
            ("BFD no peer...", self._bfd_peer),
            ("Community...", self._community),
            ("Keepalive/Hold...", self._keepalive),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover vizinho")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove_peer)
        layout.addWidget(del_btn)

    def fetch(self):
        text4 = self.session.run("display bgp peer")
        info = parsers.parse_bgp_local(text4)
        self.local_as = int(info["local_as"] or 0)
        rows = parsers.parse_bgp_peers(text4)
        seen = {r["peer"] for r in rows}

        try:
            text6 = self.session.run("display bgp ipv6 peer")
            rows += [r for r in parsers.parse_bgp_peers(text6)
                     if r["peer"] not in seen]
            if not self.local_as:
                info6 = parsers.parse_bgp_local(text6)
                self.local_as = int(info6["local_as"] or 0)
        except Exception:
            pass

        return rows

    def _on_rows(self, rows):
        self._rows = rows
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, value in enumerate(self.row_values(row)):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.table.setItem(r, c, item)
            state = row.get("state", "").lower()
            if "established" in state:
                color = Qt.darkGreen
            elif "idle" in state or "active" in state:
                color = Qt.darkGray
            else:
                color = Qt.darkYellow
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item:
                    item.setForeground(color)

        suffix = f" — AS local {self.local_as}" if self.local_as else ""
        self._set_busy(False, f"{len(rows)} vizinho(s){suffix}")

    def row_values(self, row):
        return [row["peer"], row["as"], row.get("family", "IPv4"),
                row["up_down"], row["state"], row["prefrcv"]]

    # ----------------------------------------------------------------- #
    def _require_as(self, values: dict | None = None) -> int:
        if values and values.get("local_as"):
            return int(values["local_as"])
        if self.local_as:
            return self.local_as
        QMessageBox.information(
            self, self.title,
            "O BGP ainda não está habilitado. Use 'Habilitar BGP...' "
            "informando o número do AS local.")
        return 0

    def _enable_bgp(self):
        values = SimpleForm.ask(
            "Habilitar processo BGP",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("router_id", "Router ID (opcional)", "text", "")],
            self)
        if values and values["local_as"]:
            self.apply_commands(translator.bgp_enable(
                values["local_as"], values["router_id"]))

    def _add_peer(self):
        values = SimpleForm.ask(
            "Novo vizinho BGP (IPv4)",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "IP do vizinho", "text", ""),
             ("remote_as", "AS remoto", "int", 65001),
             ("description", "Descrição (opcional)", "text", "")],
            self)
        if values and values["peer_ip"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_peer(
                    local_as, values["peer_ip"], values["remote_as"],
                    values["description"]))

    def _add_peer_ipv6(self):
        values = SimpleForm.ask(
            "Novo vizinho BGP IPv6",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "Endereço IPv6 do vizinho", "text", ""),
             ("remote_as", "AS remoto", "int", 65001),
             ("description", "Descrição (opcional)", "text", "")],
            self)
        if values and values["peer_ip"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_ipv6_peer(
                    local_as, values["peer_ip"], values["remote_as"],
                    description=values["description"]))

    def _peer_group(self):
        values = SimpleForm.ask(
            "Grupo de Peers BGP",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("group_name", "Nome do grupo", "text", ""),
             ("remote_as", "AS remoto do grupo (0 = iBGP)", "int", 0),
             ("peer_ip", "Adicionar peer ao grupo (IP, opcional)", "text", ""),
             ("remove", "Remover grupo", "bool", False)],
            self)
        if not values or not values["group_name"]:
            return
        local_as = self._require_as(values)
        if not local_as:
            return
        cmds = translator.bgp_peer_group(
            local_as, values["group_name"],
            remote_as=values["remote_as"] or None,
            remove=values["remove"])
        if values["peer_ip"] and not values["remove"]:
            cmds += translator.bgp_peer_to_group(
                local_as, values["peer_ip"], values["group_name"])
        self.apply_commands(cmds)

    def _remove_peer(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um vizinho.")
            return
        local_as = self._require_as()
        if local_as:
            self.apply_commands(translator.bgp_peer(
                local_as, row["peer"], 0, remove=True))

    def _disable_peer(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um vizinho.")
            return
        local_as = self._require_as()
        if local_as:
            self.apply_commands(translator.bgp_peer_ignore(
                local_as, row["peer"], ignore=True))

    def _enable_peer(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um vizinho.")
            return
        local_as = self._require_as()
        if local_as:
            self.apply_commands(translator.bgp_peer_ignore(
                local_as, row["peer"], ignore=False))

    def _add_network(self):
        values = SimpleForm.ask(
            "Anunciar rede no BGP",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("network", "Rede (ex.: 203.0.113.0)", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", "")],
            self)
        if values and values["network"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_network(
                    local_as, values["network"], values["mask"]))

    def _soft_reset(self):
        row = self.selected_row()
        peer_ip = row["peer"] if row else ""
        values = SimpleForm.ask(
            "BGP Soft Reset",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "IP do peer (vazio = todos)", "text", peer_ip),
             ("direction", "Direção", "choice:both|import|export", "both")],
            self)
        if values:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(
                    translator.bgp_soft_reset(
                        local_as, values["peer_ip"], values["direction"]),
                    system_view=False)

    def _route_policy(self):
        row = self.selected_row()
        peer_ip = row["peer"] if row else ""
        values = SimpleForm.ask(
            "Aplicar Route-Policy no peer",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "IP do peer", "text", peer_ip),
             ("policy_name", "Nome da route-policy", "text", ""),
             ("direction", "Direção", "choice:import|export", "import"),
             ("remove", "Remover policy", "bool", False)],
            self)
        if values and values["peer_ip"] and values["policy_name"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_route_policy(
                    local_as, values["peer_ip"], values["policy_name"],
                    values["direction"], remove=values["remove"]))

    def _bfd_peer(self):
        row = self.selected_row()
        peer_ip = row["peer"] if row else ""
        values = SimpleForm.ask(
            "BFD no vizinho BGP",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "IP do peer", "text", peer_ip),
             ("min_tx", "Intervalo TX mínimo (ms)", "int", 1000),
             ("min_rx", "Intervalo RX mínimo (ms)", "int", 1000),
             ("multiplier", "Multiplicador de detecção", "int", 3),
             ("remove", "Remover BFD", "bool", False)],
            self)
        if values and values["peer_ip"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_bfd(
                    local_as, values["peer_ip"],
                    values["min_tx"], values["min_rx"], values["multiplier"],
                    remove=values["remove"]))

    def _community(self):
        row = self.selected_row()
        peer_ip = row["peer"] if row else ""
        values = SimpleForm.ask(
            "Configurar Community no peer",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("peer_ip", "IP do peer", "text", peer_ip),
             ("send_community",
              "Enviar community",
              "choice:standard|extended|both",
              "both"),
             ("remove", "Remover configuração", "bool", False)],
            self)
        if values and values["peer_ip"]:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_community(
                    local_as, values["peer_ip"], values["send_community"],
                    remove=values["remove"]))

    def _keepalive(self):
        values = SimpleForm.ask(
            "Temporizadores BGP",
            [("local_as", "AS local", "int", self.local_as or 65000),
             ("keepalive", "Keepalive (s)", "int", 60),
             ("hold_time", "Hold Time (s)", "int", 180)],
            self)
        if values:
            local_as = self._require_as(values)
            if local_as:
                self.apply_commands(translator.bgp_keepalive(
                    local_as, values["keepalive"], values["hold_time"]))
