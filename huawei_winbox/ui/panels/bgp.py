"""Painel BGP — vizinhos IPv4 e IPv6, rotas anunciadas/recebidas e configuração."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPushButton, QMessageBox, QTableWidgetItem,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QDialogButtonBox, QTabWidget, QWidget, QGroupBox,
)

from ...core import parsers, translator
from ..forms import SimpleForm
from ..worker import run_async
from .base import BasePanel


class BgpRoutingTableDialog(QDialog):
    """Mostra rotas anunciadas e recebidas para um peer BGP."""

    def __init__(self, peer_ip: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tabela de Rotas BGP — {peer_ip}")
        self.resize(800, 560)
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._adv_edit = self._make_edit()
        self._rcv_edit = self._make_edit()
        self._tabs.addTab(self._adv_edit, "Rotas Anunciadas (Advertised)")
        self._tabs.addTab(self._rcv_edit, "Rotas Recebidas (Received)")
        layout.addWidget(self._tabs)

        self._status = QLabel("Consultando equipamento...")
        layout.addWidget(self._status)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @staticmethod
    def _make_edit() -> QPlainTextEdit:
        w = QPlainTextEdit()
        w.setObjectName("terminal")
        w.setReadOnly(True)
        return w

    def populate_advertised(self, text: str):
        self._adv_edit.setPlainText(text)

    def populate_received(self, text: str):
        self._rcv_edit.setPlainText(text)

    def set_status(self, text: str):
        self._status.setText(text)


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
            ("Rotas anunciadas...", self._show_advertised),
            ("Rotas recebidas...", self._show_received),
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

    # ----------------------------------------------------------------- #
    # Visualização de rotas por peer
    # ----------------------------------------------------------------- #

    def _need_peer(self) -> dict | None:
        row = self.selected_row()
        if not row:
            QMessageBox.information(
                self, self.title, "Selecione um vizinho na tabela.")
        return row

    def _show_advertised(self):
        row = self._need_peer()
        if not row:
            return
        self._open_routing_table(row, start_tab=0)

    def _show_received(self):
        row = self._need_peer()
        if not row:
            return
        self._open_routing_table(row, start_tab=1)

    def _open_routing_table(self, row: dict, start_tab: int = 0):
        peer_ip = row["peer"]
        family = row.get("family", "IPv4")
        is_ipv6 = "6" in family

        dlg = BgpRoutingTableDialog(peer_ip, self)
        dlg._tabs.setCurrentIndex(start_tab)
        dlg.show()

        self._set_busy(True, f"Consultando rotas de {peer_ip}...")

        def fetch():
            if is_ipv6:
                adv_cmd = (
                    f"display bgp ipv6 routing-table peer "
                    f"{peer_ip} advertised-routes")
                rcv_cmd = (
                    f"display bgp ipv6 routing-table peer "
                    f"{peer_ip} received-routes")
            else:
                adv_cmd = (
                    f"display bgp routing-table peer "
                    f"{peer_ip} advertised-routes")
                rcv_cmd = (
                    f"display bgp routing-table peer "
                    f"{peer_ip} received-routes")
            adv = self.session.run(adv_cmd, timeout=30.0)
            rcv = self.session.run(rcv_cmd, timeout=30.0)
            return adv, rcv

        def done(result):
            adv, rcv = result
            self._set_busy(False, "")
            dlg.populate_advertised(adv)
            dlg.populate_received(rcv)
            adv_count = adv.count("\n") - 4
            rcv_count = rcv.count("\n") - 4
            dlg.set_status(
                f"Anunciadas: ~{max(adv_count, 0)} prefixos  |  "
                f"Recebidas: ~{max(rcv_count, 0)} prefixos")

        def err(msg: str):
            self._set_busy(False, "")
            dlg.set_status(f"Erro: {msg}")

        run_async(fetch, done, err)

    # ----------------------------------------------------------------- #
    # Criação / edição de Route-Policy completa
    # ----------------------------------------------------------------- #

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
              "Extra (repete AS para prepend, GE/LE para prefix, etc.)",
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

        if clause == "Apenas criar nó":
            self.apply_commands(translator.route_policy_node(pn, nd, md))
        elif clause == "if-match prefix-list" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_match_prefix(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "if-match as-path-filter" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_match_as_path(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "if-match community-filter" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_match_community(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "if-match tag" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_match_tag(pn, nd, md, int(val))
            self.apply_commands(cmds)
        elif clause == "if-match interface" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_match_interface(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "apply local-preference" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_local_pref(pn, nd, md, int(val))
            self.apply_commands(cmds)
        elif clause == "apply cost (MED)" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_med(pn, nd, md, int(val))
            self.apply_commands(cmds)
        elif clause == "apply next-hop" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_next_hop(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "apply community" and val:
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_community(pn, nd, md, val)
            self.apply_commands(cmds)
        elif clause == "apply as-path prepend" and val:
            repeat = max(1, values["extra"])
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_as_path_prepend(
                pn, nd, md, int(val), repeat=repeat)
            self.apply_commands(cmds)
        elif clause == "apply origin":
            origin = val if val in ("igp", "egp", "incomplete") else "igp"
            cmds = translator.route_policy_node(pn, nd, md)
            cmds += translator.route_policy_set_origin(pn, nd, md, origin)
            self.apply_commands(cmds)

    def _as_path_filter(self):
        values = SimpleForm.ask(
            "AS-Path Filter",
            [("filter_name", "Nome do filtro (ex.: filter1 ou número 1-99)", "text", ""),
             ("index", "Índice", "int", 10),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("regexp",
              "Expressão regular (ex.: ^65001_ ou _65000$)",
              "text", ""),
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
