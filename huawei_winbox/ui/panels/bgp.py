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
            ("Anunciar rede...", self._add_network),
            ("Desabilitar peer", self._disable_peer),
            ("Habilitar peer", self._enable_peer),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        del_btn = QPushButton("Remover vizinho")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove_peer)
        layout.addWidget(del_btn)

    def fetch(self):
        # Família IPv4 — a família de cada linha vem do próprio endereço
        # do peer (parse_bgp_peers), nunca do comando que gerou a saída.
        text4 = self.session.run("display bgp peer")
        info = parsers.parse_bgp_local(text4)
        self.local_as = int(info["local_as"] or 0)
        rows = parsers.parse_bgp_peers(text4)
        seen = {r["peer"] for r in rows}

        # Família IPv6 (comando separado; falha silenciosa se não configurado).
        # Peers que já apareceram no comando IPv4 não são duplicados.
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
                # Ignora (disabled) peers em cinza
                if row.get("state", "").lower() in ("idle", "idle(admin)"):
                    for col in range(self.table.columnCount()):
                        pass  # colored below
                self.table.setItem(r, c, item)
            # Cor por estado
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
            "Novo vizinho BGP",
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
        """Desabilita (ignore) o peer sem removê-lo."""
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um vizinho.")
            return
        local_as = self._require_as()
        if local_as:
            self.apply_commands(translator.bgp_peer_ignore(
                local_as, row["peer"], ignore=True))

    def _enable_peer(self):
        """Reabilita um peer que estava com ignore."""
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
