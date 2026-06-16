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


# ─── Utilitários: parsers de tabela de rotas VRP ──────────────────────── #

_FLAG_CHARS       = frozenset("*>idshSe? ")
_ROUTE_FLAG_START = frozenset("*>idshSe?")   # char válido no início de linha de rota
_MAX_ROUTES       = 5_000                      # limite de exibição por tabela


def _is_ip_prefix(s: str) -> bool:
    """True se s parece um prefixo IPv4 ou IPv6 (com ou sem /len)."""
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$", s):
        return True
    if ":" in s and re.match(r"^[0-9a-fA-F:]{2,}(/\d{1,3})?$", s):
        return True
    return False


# ── Formato colunar fixo (IPv4): Network NextHop MED LocPrf PrefVal Path ─ #

def _parse_bgp_route_table_columnar(text: str) -> list[dict]:
    lines = text.splitlines()

    # Fase 1: localizar cabeçalho e posições brutas das colunas
    col_net = col_nh = col_med = col_lp = col_pv = col_path = -1
    hdr_idx = -1
    for i, line in enumerate(lines):
        lo = line.lower()
        if "network" in lo and "nexthop" in lo and "med" in lo:
            hdr_idx = i
            col_net  = lo.find("network")
            col_nh   = lo.find("nexthop")
            col_med  = lo.find("med")
            col_lp   = lo.find("locprf")
            col_pv   = lo.find("prefval") if "prefval" in lo else -1
            col_path = lo.find("path")
            break

    # Fase 2: calcular offset (chars de flag deslocam dados vs cabeçalho)
    offset = 0
    if hdr_idx >= 0 and col_net >= 0:
        for line in lines[hdr_idx + 1:]:
            if not line.strip():
                continue
            lo = line.lower()
            if "network" in lo and "nexthop" in lo:
                continue
            fi = 0
            while fi < len(line) and fi < 10 and line[fi] in _FLAG_CHARS:
                fi += 1
            tok = line[fi:].split()
            if not tok or not _is_ip_prefix(tok[0]):
                continue
            pos = line.find(tok[0], max(0, fi - 1))
            if pos >= 0:
                offset = pos - col_net
            break

    # Ajustar todas as colunas pelo offset
    def adj(c: int) -> int:
        return (c + offset) if c >= 0 else -1

    a_nh   = adj(col_nh)
    a_med  = adj(col_med)
    a_lp   = adj(col_lp)
    a_pv   = adj(col_pv)
    a_path = adj(col_path)

    # Fase 3: parsear linhas de dados com fatiamento posicional
    routes: list[dict] = []
    for raw in lines:
        if not raw.strip():
            continue
        lo = raw.lower()
        if "network" in lo and "nexthop" in lo:
            continue

        fi = 0
        while fi < len(raw) and fi < 10 and raw[fi] in _FLAG_CHARS:
            fi += 1
        flags = raw[:fi].strip()

        tokens = raw[fi:].split()
        if not tokens or not _is_ip_prefix(tokens[0]):
            continue
        net = tokens[0]

        if a_nh > 0 and len(raw) > a_nh:
            nexthop = raw[a_nh: a_med if a_med > 0 else None].strip()
            # sanidade: nexthop deve ser endereço IP
            nh_tok = nexthop.split()[0] if nexthop else ""
            if not re.match(r"^[\d.:a-fA-F]+$", nh_tok):
                nexthop = tokens[1] if len(tokens) > 1 else ""
            med_end = a_lp  if a_lp  > 0 else (a_pv if a_pv > 0 else a_path)
            lp_end  = a_pv  if a_pv  > 0 else a_path
            med    = raw[a_med: med_end].strip() if a_med > 0 else ""
            locprf = raw[a_lp:  lp_end ].strip() if a_lp  > 0 else ""
            path   = raw[a_path:].strip()          if a_path > 0 else ""
        else:
            # fallback sem cabeçalho
            nexthop = tokens[1] if len(tokens) > 1 else ""
            med = locprf = ""
            path = " ".join(tokens[2:]) if len(tokens) > 2 else ""

        routes.append({
            "flags":   flags,
            "network": net,
            "nexthop": nexthop,
            "med":     med,
            "locprf":  locprf,
            "path":    path,
        })
    return routes


# ── Formato chave-valor multi-linha (IPv6 VRP) ───────────────────────────── #

def _kv_to_route(r: dict) -> dict:
    net = r["network_addr"]
    if r["prefixlen"]:
        net = f"{net}/{r['prefixlen']}"
    return {
        "flags":   r["flags"],
        "network": net,
        "nexthop": r["nexthop"],
        "med":     r["med"],
        "locprf":  r["locprf"],
        "path":    r["path"],
    }


def _parse_bgp_route_table_kv(text: str) -> list[dict]:
    """Parser para o formato chave-valor multi-linha do IPv6 VRP.

    *>  Network  : 2804:4354::  PrefixLen : 32
        NextHop  : 2001:db8::1  LocPrf    :
        MED      : 0             PrefVal   : 0
        Path/Ogn : 65000 65001i
    """
    routes: list[dict] = []
    cur: dict | None = None

    for line in text.splitlines():
        # Nova rota: primeiro char é flag E linha contém "Network :"
        if line and line[0] in _ROUTE_FLAG_START and re.search(r'\bNetwork\s*:', line):
            if cur:
                routes.append(_kv_to_route(cur))
            fm = re.match(r'^([*>idshSe?][*>idshSe? ]*)', line)
            flags = fm.group(1).strip() if fm else ""
            nm = re.search(r'\bNetwork\s*:\s*(\S*)', line)
            pm = re.search(r'\bPrefixLen\s*:\s*(\d+)', line)
            cur = {
                "flags":        flags,
                "network_addr": nm.group(1).strip() if nm else "",
                "prefixlen":    pm.group(1)          if pm else "",
                "nexthop": "",
                "locprf":  "",
                "med":     "",
                "path":    "",
            }
            continue

        if cur is None:
            continue

        m = re.search(r'\bNextHop\s*:\s*(\S+)', line, re.IGNORECASE)
        if m:
            cur["nexthop"] = m.group(1)

        m = re.search(r'\bLocPrf\s*:\s*(\d+)', line, re.IGNORECASE)
        if m:
            cur["locprf"] = m.group(1)

        m = re.search(r'\bMED\s*:\s*(\d+)', line, re.IGNORECASE)
        if m:
            cur["med"] = m.group(1)

        # Path/Ogn pode ter múltiplos ASes: captura tudo após ":"
        m = re.search(r'\bPath/Ogn\s*:\s*(.+)', line, re.IGNORECASE)
        if m:
            cur["path"] = m.group(1).strip()

    if cur:
        routes.append(_kv_to_route(cur))

    return routes


# ── Ponto de entrada unificado ───────────────────────────────────────────── #

def _parse_bgp_route_table(text: str) -> tuple[list[dict], int]:
    """Detecta o formato VRP e delega ao parser adequado.

    Formato KV multi-linha (IPv6): detectado por 'Network :' no texto.
    Formato colunar (IPv4):        cabeçalho com Network/NextHop/MED.
    """
    total_m = re.search(r"Total Number of Routes:\s*(\d+)", text)
    total = int(total_m.group(1)) if total_m else 0

    if re.search(r'\bNetwork\s*:', text):
        routes = _parse_bgp_route_table_kv(text)
    else:
        routes = _parse_bgp_route_table_columnar(text)

    if len(routes) > _MAX_ROUTES:
        routes = routes[:_MAX_ROUTES]

    return routes, total or len(routes)


def _parse_peer_verbose(text: str) -> dict:
    """Extrai campos relevantes de 'display bgp peer X verbose'.

    Cobre múltiplos formatos VRP (CE, NE, ATN, S-series).
    """
    def find(*patterns: str, default: str = "—") -> str:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if m:
                return m.group(1).strip()
        return default

    return {
        "type": find(r"Type:\s+(.+?)\s*$"),
        "router_id": find(r"remote router ID[:\s]+(\S+)"),
        "state": find(r"BGP current state:\s+(\S[^\n,]*)"),
        "hold_cfg": find(
            r"Configured:\s*Active Hold Time:\s*(\d+\s*Sec)",
            r"Hold Time[:\s]+(\d+\s*[Ss]ec(?:onds)?)"),
        "keep_cfg": find(
            r"Keepalive Time[:\s]+(\d+\s*[Ss]ec(?:onds)?)"),
        "bfd": find(
            r"BFD[:\s]+(\S+)",
            r"Peer.s BFD state[:\s]+(\S+)"),
        "community": find(
            r"Configured Send Community[:\s]+(\S+)",
            r"Send Community[:\s]+(\S+)"),
        "pol_import": find(
            r"Policy for receiving routing[:\s]+(.+?)\s*$",
            r"Route Policy for receiving[:\s]+(.+?)\s*$",
            r"Import route-policy[:\s]+(.+?)\s*$"),
        "pol_export": find(
            r"Policy for sending routing[:\s]+(.+?)\s*$",
            r"Route Policy for sending[:\s]+(.+?)\s*$",
            r"Export route-policy[:\s]+(.+?)\s*$"),
        "rcv_active": find(
            r"Number of received active routes[:\s]+(\d+)",
            r"Received active routes total[:\s]+(\d+)",
            r"Active routes[:\s]+(\d+)"),
        "accepted": find(
            r"Number of accepted routes[:\s]+(\d+)",
            r"Received routes valid[:\s]+(\d+)",
            r"Accepted routes[:\s]+(\d+)"),
        "sent": find(
            r"Number of sent routes[:\s]+(\d+)",
            r"Routes sent[:\s]+(\d+)",
            r"Advertised routes[:\s]+(\d+)"),
        "conn_iface": find(
            r"Connect-interface:\s*(\S+)(?!\s+has)"),
        "update_grp": find(
            r"Update-group ID[:\s]+(\d+)"),
    }


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
        self._is_ipv6 = ":" in row.get("peer", "")

        self.setWindowTitle(f"Peer BGP — {self._peer_ip}")
        self.resize(940, 700)

        root = QVBoxLayout(self)

        # ── Tabs principais ── #
        tabs = QTabWidget()
        root.addWidget(tabs)

        # ── Tab 1: Informações + configuração ── #
        info_tab = QWidget()
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setWidget(info_tab)
        info_vbox = QVBoxLayout(info_tab)
        info_vbox.setSpacing(8)
        tabs.addTab(info_scroll, "Visão Geral")

        # Informações básicas (da tabela)
        state = row.get("state", "")
        cor = "#2e8b57" if "established" in state.lower() else "#c0392b"
        basic_grp = QGroupBox("Informações do Vizinho")
        basic_grid = QGridLayout(basic_grp)
        basic_pairs = [
            ("IP do Vizinho",       self._peer_ip),
            ("AS Remoto",           row.get("as", "—")),
            ("Família",             row.get("family", "IPv4")),
            ("Estado",
             f'<span style="color:{cor};font-weight:bold">{state or "—"}</span>'),
            ("Tempo Ativo",         row.get("up_down", "—")),
            ("Prefixos Recebidos",  row.get("prefrcv", "—")),
        ]
        for i, (label, value) in enumerate(basic_pairs):
            col, row_i = (i % 2) * 2, i // 2
            lbl = QLabel(f"<b>{label}:</b>")
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            basic_grid.addWidget(lbl, row_i, col)
            basic_grid.addWidget(val, row_i, col + 1)
        info_vbox.addWidget(basic_grp)

        # Configuração detalhada (carregada via verbose)
        cfg_grp = QGroupBox("Configuração Detalhada do Peer")
        cfg_layout = QVBoxLayout(cfg_grp)
        cfg_bar = QHBoxLayout()
        self._cfg_load_btn = QPushButton("Carregar Configuração")
        self._cfg_load_btn.clicked.connect(self._load_config)
        self._cfg_status = QLabel("Clique em 'Carregar Configuração'.")
        cfg_bar.addWidget(self._cfg_load_btn)
        cfg_bar.addWidget(self._cfg_status)
        cfg_bar.addStretch(1)
        cfg_layout.addLayout(cfg_bar)
        self._cfg_grid = QGridLayout()
        cfg_layout.addLayout(self._cfg_grid)
        info_vbox.addWidget(cfg_grp)

        # Ações rápidas
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
        info_vbox.addWidget(act_grp)
        info_vbox.addStretch(1)

        # ── Tab 2: Rotas Anunciadas ── #
        adv_tab = QWidget()
        adv_layout = QVBoxLayout(adv_tab)
        adv_bar = QHBoxLayout()
        self._adv_load_btn = QPushButton("Carregar Rotas Anunciadas")
        self._adv_load_btn.clicked.connect(self._load_advertised)
        self._adv_status = QLabel("Clique para consultar o equipamento.")
        adv_bar.addWidget(self._adv_load_btn)
        adv_bar.addWidget(self._adv_status)
        adv_bar.addStretch(1)
        adv_layout.addLayout(adv_bar)
        self._adv_table = self._make_route_table()
        adv_layout.addWidget(self._adv_table)
        tabs.addTab(adv_tab, "Rotas Anunciadas")

        # ── Tab 3: Rotas Aceitas ── #
        rcv_tab = QWidget()
        rcv_layout = QVBoxLayout(rcv_tab)
        rcv_bar = QHBoxLayout()
        self._rcv_load_btn = QPushButton("Carregar Rotas Aceitas")
        self._rcv_load_btn.clicked.connect(self._load_accepted)
        self._rcv_status = QLabel("Clique para consultar o equipamento.")
        rcv_bar.addWidget(self._rcv_load_btn)
        rcv_bar.addWidget(self._rcv_status)
        rcv_bar.addStretch(1)
        rcv_layout.addLayout(rcv_bar)
        self._rcv_table = self._make_route_table()
        rcv_layout.addWidget(self._rcv_table)
        tabs.addTab(rcv_tab, "Rotas Aceitas")

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

    # ── fetch configuração verbose ───────────────────────────────────── #

    def _load_config(self):
        self._cfg_load_btn.setEnabled(False)
        self._cfg_load_btn.setText("Consultando…")
        self._cfg_status.setText("Aguardando resposta do equipamento…")
        peer = self._peer_ip
        is6  = self._is_ipv6

        def fetch():
            verb_cmd = (
                f"display bgp ipv6 peer {peer} verbose" if is6
                else f"display bgp peer {peer} verbose"
            )
            verbose = self._session.run(verb_cmd, timeout=15.0)
            # Route-policy vem da configuração ativa
            try:
                cfg = self._session.run(
                    f"display current-configuration bgp | include {peer}",
                    timeout=10.0)
            except Exception:
                cfg = ""
            return verbose, cfg

        def done(result):
            verbose, cfg = result
            self._cfg_load_btn.setEnabled(True)
            self._cfg_load_btn.setText("Atualizar Configuração")
            info = _parse_peer_verbose(verbose)

            # Sobrescreve route-policies com dados da configuração ativa
            for line in cfg.splitlines():
                m = re.search(
                    r'peer\s+\S+\s+route-policy\s+(\S+)\s+(import|export)',
                    line, re.IGNORECASE)
                if m:
                    if m.group(2).lower() == "import":
                        info["pol_import"] = m.group(1)
                    else:
                        info["pol_export"] = m.group(1)

            # limpa grade anterior
            while self._cfg_grid.count():
                item = self._cfg_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            labels = [
                ("Tipo de sessão",          info["type"]),
                ("Router ID remoto",        info["router_id"]),
                ("Estado atual",           info["state"]),
                ("Hold Time",              info["hold_cfg"]),
                ("Keepalive",              info["keep_cfg"]),
                ("BFD",                    info["bfd"]),
                ("Community enviada",      info["community"]),
                ("Route-Policy import",    info["pol_import"]),
                ("Route-Policy export",    info["pol_export"]),
                ("Rotas recebidas ativas", info["rcv_active"]),
                ("Rotas aceitas",          info["accepted"]),
                ("Rotas enviadas",         info["sent"]),
                ("Interface de conexão",   info["conn_iface"]),
                ("Update Group ID",        info["update_grp"]),
            ]
            found = sum(1 for _, v in labels if v != "—")
            for i, (lbl_text, val_text) in enumerate(labels):
                col, ri = (i % 2) * 2, i // 2
                lbl = QLabel(f"<b>{lbl_text}:</b>")
                val = QLabel(val_text)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self._cfg_grid.addWidget(lbl, ri, col)
                self._cfg_grid.addWidget(val, ri, col + 1)
            if found == 0 and verbose.strip():
                raw_lbl = QLabel(
                    "<b>Saída do equipamento (formato não reconhecido):</b>")
                self._cfg_grid.addWidget(raw_lbl, len(labels) // 2 + 1, 0, 1, 4)
                from PySide6.QtWidgets import QPlainTextEdit
                raw_edit = QPlainTextEdit(verbose[:600])
                raw_edit.setObjectName("terminal")
                raw_edit.setReadOnly(True)
                raw_edit.setMaximumHeight(120)
                self._cfg_grid.addWidget(
                    raw_edit, len(labels) // 2 + 2, 0, 1, 4)
            self._cfg_status.setText(
                f"Configuração carregada ({found} campo(s) identificado(s)).")

        def err(msg: str):
            self._cfg_load_btn.setEnabled(True)
            self._cfg_load_btn.setText("Tentar novamente")
            self._cfg_status.setText(f"Erro: {msg}")

        run_async(fetch, done, err)

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

    # ── fetch rotas aceitas ──────────────────────────────────────────── #

    def _load_accepted(self):
        self._rcv_load_btn.setEnabled(False)
        self._rcv_load_btn.setText("Consultando…")
        self._rcv_status.setText("Aguardando resposta do equipamento…")

        peer = self._peer_ip
        is6  = self._is_ipv6

        def fetch():
            cmd = (
                f"display bgp ipv6 routing-table peer {peer} accepted-routes"
                if is6 else
                f"display bgp routing-table peer {peer} accepted-routes"
            )
            return self._session.run(cmd, timeout=60.0)

        def done(text: str):
            self._rcv_load_btn.setEnabled(True)
            self._rcv_load_btn.setText("Atualizar Rotas Aceitas")
            routes, total = _parse_bgp_route_table(text)
            _fill_route_table(self._rcv_table, routes, total, self._rcv_status)

        def err(msg: str):
            self._rcv_load_btn.setEnabled(True)
            self._rcv_load_btn.setText("Atualizar Rotas Aceitas")
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
