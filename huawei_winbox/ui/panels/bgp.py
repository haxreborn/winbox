"""Painel BGP — vizinhos IPv4 e IPv6, rotas e configuração.

Duplo clique num peer abre o diálogo de detalhes com rotas anunciadas e
recebidas carregadas sob demanda (clique num botão por vez).
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPushButton, QMessageBox, QTableWidgetItem,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDialogButtonBox, QTabWidget, QWidget, QGroupBox,
    QTableWidget, QHeaderView, QSizePolicy, QGridLayout,
    QScrollArea,
)

from ...core import parsers, translator
from ..forms import SimpleForm
from ..worker import run_async
from .base import BasePanel


# ─── Utilitário: parser de tabela de rotas VRP ─────────────────────────── #

def _parse_bgp_route_table(text: str) -> tuple[list[dict], int]:
    """Converte a saída de 'display bgp routing-table peer X …' em linhas.

    Retorna (lista_de_rotas, total_declarado).
    Cada rota: flags, network, nexthop, med, locprf, path.
    """
    total_m = re.search(r"Total Number of Routes:\s*(\d+)", text)
    total = int(total_m.group(1)) if total_m else 0

    route_re = re.compile(
        r"^\s*([*>dishSi ]{1,4})\s+"   # flags
        r"(\S+)\s+"                      # network/prefix
        r"(\S+)\s+"                      # nexthop
        r"(\S*)\s+"                      # MED
        r"(\S*)\s+"                      # LocPrf
        r"(\S*)\s+"                      # PrefVal
        r"(.+?)\s*$"                     # Path/Ogn
    )
    routes = []
    for line in text.splitlines():
        m = route_re.match(line)
        if m:
            routes.append({
                "flags":   m.group(1).strip(),
                "network": m.group(2),
                "nexthop": m.group(3),
                "med":     m.group(4),
                "locprf":  m.group(5),
                "path":    m.group(7).strip(),
            })
    return routes, total or len(routes)


def _fill_route_table(table: QTableWidget, routes: list[dict], total: int,
                      status_lbl: QLabel):
    table.setRowCount(0)
    for r in routes:
        row = table.rowCount()
        table.insertRow(row)
        for c, key in enumerate(("flags", "network", "nexthop",
                                 "med", "locprf", "path")):
            item = QTableWidgetItem(r.get(key, ""))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, c, item)
    status_lbl.setText(
        f"{len(routes)} rota(s) exibida(s)"
        + (f" de {total} no total" if total > len(routes) else ""))


# ─── Diálogo de detalhes de um peer BGP ────────────────────────────────── #

class BgpPeerDetailDialog(QDialog):
    """Exibe informações completas de um vizinho BGP.

    As rotas anunciadas e recebidas são carregadas sob demanda —
    cada seção tem seu próprio botão 'Carregar'.
    """

    _ROUTE_COLS = ["Status", "Rede/Prefixo", "Next Hop",
                   "MED", "LocPrf", "AS Path / Origem"]

    def __init__(self, row: dict, session, local_as: int, parent=None):
        super().__init__(parent)
        self._row = row
        self._session = session
        self._local_as = local_as
        self._peer_ip = row["peer"]
        self._is_ipv6 = "6" in row.get("family", "IPv4")

        self.setWindowTitle(f"Peer BGP — {self._peer_ip}")
        self.resize(900, 640)

        root = QVBoxLayout(self)

        # ── Scroll para caber em telas menores ── #
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        root.addWidget(scroll)
        vbox = QVBoxLayout(content)
        vbox.setSpacing(8)

        # ── Informações do peer ── #
        info_grp = QGroupBox("Informações do Vizinho")
        info_grid = QGridLayout(info_grp)
        state = row.get("state", "")
        cor = "#2e8b57" if "established" in state.lower() else "#c0392b"
        pairs = [
            ("IP do Vizinho",    self._peer_ip),
            ("AS Remoto",        row.get("as", "—")),
            ("Família",          row.get("family", "IPv4")),
            ("Estado",
             f'<span style="color:{cor};font-weight:bold">{state or "—"}</span>'),
            ("Tempo Ativo",      row.get("up_down", "—")),
            ("Prefixos Recebidos", row.get("prefrcv", "—")),
        ]
        for i, (label, value) in enumerate(pairs):
            col, row_i = (i % 2) * 2, i // 2
            lbl = QLabel(f"<b>{label}:</b>")
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_grid.addWidget(lbl, row_i, col)
            info_grid.addWidget(val, row_i, col + 1)
        vbox.addWidget(info_grp)

        # ── Rotas Anunciadas ── #
        adv_grp = QGroupBox("Rotas Anunciadas para este Peer")
        adv_layout = QVBoxLayout(adv_grp)
        adv_bar = QHBoxLayout()
        self._adv_load_btn = QPushButton("Carregar Rotas Anunciadas")
        self._adv_load_btn.clicked.connect(self._load_advertised)
        self._adv_status = QLabel("Clique em 'Carregar' para consultar.")
        adv_bar.addWidget(self._adv_load_btn)
        adv_bar.addWidget(self._adv_status)
        adv_bar.addStretch(1)
        adv_layout.addLayout(adv_bar)
        self._adv_table = self._make_route_table()
        adv_layout.addWidget(self._adv_table)
        vbox.addWidget(adv_grp)

        # ── Rotas Recebidas ── #
        rcv_grp = QGroupBox("Rotas Recebidas deste Peer")
        rcv_layout = QVBoxLayout(rcv_grp)
        rcv_bar = QHBoxLayout()
        self._rcv_load_btn = QPushButton("Carregar Rotas Recebidas")
        self._rcv_load_btn.clicked.connect(self._load_received)
        self._rcv_status = QLabel("Clique em 'Carregar' para consultar.")
        rcv_bar.addWidget(self._rcv_load_btn)
        rcv_bar.addWidget(self._rcv_status)
        rcv_bar.addStretch(1)
        rcv_layout.addLayout(rcv_bar)
        self._rcv_table = self._make_route_table()
        rcv_layout.addWidget(self._rcv_table)
        vbox.addWidget(rcv_grp)

        # ── Ações rápidas ── #
        act_grp = QGroupBox("Ações Rápidas")
        act_bar = QHBoxLayout(act_grp)
        for text, slot in [
            ("Desabilitar Peer", self._disable),
            ("Habilitar Peer",   self._enable),
            ("Soft Reset...",    self._soft_reset),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            act_bar.addWidget(btn)
        act_bar.addStretch(1)
        vbox.addWidget(act_grp)

        # ── Fechar ── #
        close_btns = QDialogButtonBox(QDialogButtonBox.Close)
        close_btns.rejected.connect(self.reject)
        root.addWidget(close_btns)

    # ── helpers ───────────────────────────────────────────────────────── #

    def _make_route_table(self) -> QTableWidget:
        t = QTableWidget(0, len(self._ROUTE_COLS))
        t.setHorizontalHeaderLabels(self._ROUTE_COLS)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(160)
        t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return t

    def _set_loading(self, btn: QPushButton, loading: bool):
        btn.setEnabled(not loading)
        btn.setText(
            "Consultando…" if loading
            else btn.text().replace("Consultando…",
                                    "Carregar " + btn.objectName()))

    # ── fetch anunciadas ─────────────────────────────────────────────── #

    def _load_advertised(self):
        self._adv_load_btn.setEnabled(False)
        self._adv_load_btn.setText("Consultando…")
        self._adv_status.setText("Aguardando resposta do equipamento…")

        peer = self._peer_ip
        is6 = self._is_ipv6

        def fetch():
            cmd = (
                f"display bgp ipv6 routing-table peer {peer} advertised-routes"
                if is6 else
                f"display bgp routing-table peer {peer} advertised-routes"
            )
            return self._session.run(cmd, timeout=30.0)

        def done(text: str):
            self._adv_load_btn.setEnabled(True)
            self._adv_load_btn.setText("Atualizar Rotas Anunciadas")
            routes, total = _parse_bgp_route_table(text)
            _fill_route_table(self._adv_table, routes, total, self._adv_status)

        def err(msg: str):
            self._adv_load_btn.setEnabled(True)
            self._adv_load_btn.setText("Atualizar Rotas Anunciadas")
            self._adv_status.setText(f"Erro: {msg}")

        run_async(fetch, done, err)

    # ── fetch recebidas ──────────────────────────────────────────────── #

    def _load_received(self):
        self._rcv_load_btn.setEnabled(False)
        self._rcv_load_btn.setText("Consultando…")
        self._rcv_status.setText("Aguardando resposta do equipamento…")

        peer = self._peer_ip
        is6 = self._is_ipv6

        def fetch():
            cmd = (
                f"display bgp ipv6 routing-table peer {peer} received-routes"
                if is6 else
                f"display bgp routing-table peer {peer} received-routes"
            )
            return self._session.run(cmd, timeout=30.0)

        def done(text: str):
            self._rcv_load_btn.setEnabled(True)
            self._rcv_load_btn.setText("Atualizar Rotas Recebidas")
            routes, total = _parse_bgp_route_table(text)
            _fill_route_table(self._rcv_table, routes, total, self._rcv_status)

        def err(msg: str):
            self._rcv_load_btn.setEnabled(True)
            self._rcv_load_btn.setText("Atualizar Rotas Recebidas")
            self._rcv_status.setText(f"Erro: {msg}")

        run_async(fetch, done, err)

    # ── ações rápidas ────────────────────────────────────────────────── #

    def _disable(self):
        if self._local_as:
            cmds = translator.bgp_peer_ignore(
                self._local_as, self._peer_ip, ignore=True)
            from ..command_preview import CommandPreviewDialog
            if CommandPreviewDialog.confirm(cmds, self):
                self._session.configure(cmds)

    def _enable(self):
        if self._local_as:
            cmds = translator.bgp_peer_ignore(
                self._local_as, self._peer_ip, ignore=False)
            from ..command_preview import CommandPreviewDialog
            if CommandPreviewDialog.confirm(cmds, self):
                self._session.configure(cmds)

    def _soft_reset(self):
        if not self._local_as:
            return
        values = SimpleForm.ask(
            "Soft Reset",
            [("direction", "Direção", "choice:both|import|export", "both")],
            self)
        if values:
            cmds = translator.bgp_soft_reset(
                self._local_as, self._peer_ip, values["direction"])
            from ..command_preview import CommandPreviewDialog
            if CommandPreviewDialog.confirm(cmds, self, system_view=False):
                self._session.run_many(cmds)


# ─── Painel principal BGP ─────────────────────────────────────────────── #

class BgpPanel(BasePanel):
    title = "BGP"
    columns = ["Vizinho", "AS remoto", "Família",
               "Up/Down", "Estado", "Prefixos"]

    def __init__(self, session, parent=None):
        self.local_as = 0
        super().__init__(session, parent)
        self.table.itemDoubleClicked.connect(self._open_peer_detail)

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
            ("Aplicar Route-Policy...", self._route_policy),
            ("Criar Route-Policy...", self._create_route_policy),
            ("AS-Path Filter...", self._as_path_filter),
            ("Community Filter...", self._community_filter),
            ("Prefix List...", self._prefix_list),
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
            color = (Qt.darkGreen if "established" in state
                     else Qt.darkGray if "idle" in state or "active" in state
                     else Qt.darkYellow)
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                if it:
                    it.setForeground(color)

        suffix = f" — AS local {self.local_as}" if self.local_as else ""
        self._set_busy(False, f"{len(rows)} vizinho(s){suffix}")

    def row_values(self, row):
        return [row["peer"], row["as"], row.get("family", "IPv4"),
                row["up_down"], row["state"], row["prefrcv"]]

    # ── duplo clique abre detalhe do peer ──────────────────────────── #

    def _open_peer_detail(self, item):
        row = item.data(Qt.UserRole)
        if row:
            dlg = BgpPeerDetailDialog(row, self.session, self.local_as, self)
            dlg.exec()

    # ─────────────────────────────────────────────────────────────────── #
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

    # ── Route-Policy / prefix-list / filtros ─────────────────────────── #

    def _create_route_policy(self):
        values = SimpleForm.ask(
            "Criar / Editar Route-Policy",
            [("policy_name", "Nome da Route-Policy", "text", ""),
             ("node", "Nó (número)", "int", 10),
             ("mode", "Modo do nó", "choice:permit|deny", "permit"),
             ("clause",
              "Cláusula (match/set)",
              "choice:"
              "Apenas criar nó|"
              "if-match prefix-list|"
              "if-match as-path-filter|"
              "if-match community-filter|"
              "if-match tag|"
              "if-match interface|"
              "apply local-preference|"
              "apply cost (MED)|"
              "apply next-hop|"
              "apply community|"
              "apply as-path prepend|"
              "apply origin",
              "Apenas criar nó"),
             ("value",
              "Valor da cláusula (nome de lista, IP, número, etc.)",
              "text", ""),
             ("extra",
              "Extra (repete AS p/ prepend)",
              "int", 1),
             ("remove", "Remover nó", "bool", False)],
            self)
        if not values or not values["policy_name"]:
            return

        pn = values["policy_name"]
        nd = values["node"]
        md = values["mode"]
        clause = values["clause"]
        val = values["value"].strip()

        if values["remove"]:
            self.apply_commands(
                translator.route_policy_node(pn, nd, md, remove=True))
            return

        dispatch = {
            "Apenas criar nó":
                lambda: translator.route_policy_node(pn, nd, md),
            "if-match prefix-list":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_match_prefix(pn, nd, md, val))
                if val else None,
            "if-match as-path-filter":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_match_as_path(pn, nd, md, val))
                if val else None,
            "if-match community-filter":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_match_community(pn, nd, md, val))
                if val else None,
            "if-match tag":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_match_tag(pn, nd, md, int(val)))
                if val else None,
            "if-match interface":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_match_interface(pn, nd, md, val))
                if val else None,
            "apply local-preference":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_local_pref(pn, nd, md, int(val)))
                if val else None,
            "apply cost (MED)":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_med(pn, nd, md, int(val)))
                if val else None,
            "apply next-hop":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_next_hop(pn, nd, md, val))
                if val else None,
            "apply community":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_community(pn, nd, md, val))
                if val else None,
            "apply as-path prepend":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_as_path_prepend(
                             pn, nd, md, int(val),
                             repeat=max(1, values["extra"])))
                if val else None,
            "apply origin":
                lambda: (translator.route_policy_node(pn, nd, md)
                         + translator.route_policy_set_origin(
                             pn, nd, md,
                             val if val in ("igp", "egp", "incomplete") else "igp")),
        }
        fn = dispatch.get(clause)
        if fn:
            cmds = fn()
            if cmds:
                self.apply_commands(cmds)

    def _as_path_filter(self):
        values = SimpleForm.ask(
            "AS-Path Filter",
            [("filter_name",
              "Nome do filtro (ex.: filter1 ou número 1-99)", "text", ""),
             ("index", "Índice", "int", 10),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("regexp",
              "Expressão regular (ex.: ^65001_ ou _65000$)", "text", ""),
             ("remove", "Remover entrada", "bool", False)],
            self)
        if values and values["filter_name"]:
            self.apply_commands(translator.ip_as_path_filter(
                values["filter_name"], values["index"], values["action"],
                values["regexp"], remove=values["remove"]))

    def _community_filter(self):
        values = SimpleForm.ask(
            "Community Filter",
            [("filter_name", "Nome do filtro", "text", ""),
             ("index", "Índice", "int", 10),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("community",
              "Community (ex.: 65000:100 ou internet ou no-export)",
              "text", ""),
             ("remove", "Remover entrada", "bool", False)],
            self)
        if values and values["filter_name"] and values["community"]:
            self.apply_commands(translator.ip_community_filter(
                values["filter_name"], values["index"], values["action"],
                values["community"], remove=values["remove"]))

    def _prefix_list(self):
        values = SimpleForm.ask(
            "IP Prefix List",
            [("name", "Nome da lista", "text", ""),
             ("index", "Índice", "int", 10),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("prefix", "Prefixo (ex.: 10.0.0.0/8)", "text", ""),
             ("ge", "GE — comprimento mínimo (0 = não usar)", "int", 0),
             ("le", "LE — comprimento máximo (0 = não usar)", "int", 0),
             ("remove", "Remover entrada", "bool", False)],
            self)
        if values and values["name"] and values["prefix"]:
            self.apply_commands(translator.ip_prefix_list(
                values["name"], values["index"], values["action"],
                values["prefix"],
                ge=values["ge"] or None,
                le=values["le"] or None,
                remove=values["remove"]))
