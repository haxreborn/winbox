"""Painel Firewall / NAT (avançado) — editor rule-by-rule de ACL,
traffic-policy (MQC) e NAT, com assistente de CGNAT por redirecionamento.

Abas:

* **ACL** — todas as ACLs e suas regras (``display acl all``); criar regra
  básica/avançada, remover regra ou ACL inteira, aplicar como
  ``traffic-filter`` numa interface.
* **Traffic-Policy (MQC)** — classificadores, comportamentos e políticas
  (``display traffic classifier|behavior|policy``); inclui o **Assistente
  CGNAT** que monta toda a cadeia ACL→classifier→behavior(redirect
  ip-nexthop)→policy e a aplica globalmente, como nos NE8000.
* **NAT** — NAT de saída (easy-ip/PAT por ACL) e redirecionamento de portas
  (NAT server / port-forward).

Os painéis simples *ACL / Firewall*, *QoS* e *NAT* continuam existindo para
tarefas rápidas; este é o editor completo.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton, QLabel,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
)

from ...core import parsers, translator
from ..command_preview import CommandPreviewDialog
from ..forms import SimpleForm
from ..worker import run_async


def _table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setSelectionMode(QTableWidget.SingleSelection)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class FirewallPanel(QWidget):
    title = "Firewall / NAT"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._loaded: set[int] = set()
        self._acl_rows: list[dict] = []
        self._mqc_rows: list[dict] = []
        self._nat_rows: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel(
            "<b>Firewall / NAT (avançado)</b> — ACL, traffic-policy (MQC) "
            "e NAT, regra a regra. Inclui assistente de CGNAT."))
        top.addStretch(1)
        self.status_lbl = QLabel("")
        top.addWidget(self.status_lbl)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_acl_tab(), "ACL")
        self.tabs.addTab(self._build_mqc_tab(), "Traffic-Policy (MQC)")
        self.tabs.addTab(self._build_nat_tab(), "NAT")
        self.tabs.currentChanged.connect(self._on_tab)
        root.addWidget(self.tabs)

        self._on_tab(0)

    def _on_tab(self, idx: int):
        if idx in self._loaded:
            return
        self._loaded.add(idx)
        (self.refresh_acl, self.refresh_mqc, self.refresh_nat)[idx]()

    # ================================================================== #
    # Aba ACL
    # ================================================================== #
    def _build_acl_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        for text, slot in [("Atualizar", self.refresh_acl),
                           ("Nova regra...", self._acl_rule),
                           ("Aplicar na interface...", self._acl_apply)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        for text, slot in [("Remover regra", self._acl_del_rule),
                           ("Remover ACL", self._acl_del_acl)]:
            b = QPushButton(text)
            b.setObjectName("dangerBtn")
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Filtrar:"))
        self.acl_find = QLineEdit()
        self.acl_find.setObjectName("findBox")
        self.acl_find.setPlaceholderText("Find")
        self.acl_find.setClearButtonEnabled(True)
        self.acl_find.textChanged.connect(
            lambda: self._filter(self.acl_table, self.acl_find))
        bar.addWidget(self.acl_find)
        lay.addLayout(bar)
        self.acl_table = _table(["ACL", "Tipo", "Regra", "Ação", "Detalhe"])
        lay.addWidget(self.acl_table)
        return w

    def refresh_acl(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando ACLs...")

        def fetch():
            return parsers.parse_acls(
                self.session.run("display acl all", timeout=20.0))

        def done(rows):
            self._acl_rows = rows
            self._fill(self.acl_table,
                       [[r["acl"], r["type"], r["rule"], r["action"],
                         r["detail"]] for r in rows])
            self._filter(self.acl_table, self.acl_find)
            self._set_busy(False, f"{len(rows)} regra(s) de ACL")

        run_async(fetch, done, self._fail)

    def _acl_rule(self):
        values = SimpleForm.ask(
            "Nova regra de ACL",
            [("acl", "Número da ACL (2000-2999 básica, 3000-3999 avançada)",
              "int", 3000),
             ("mode", "Tipo", "choice:básica|avançada", "avançada"),
             ("action", "Ação", "choice:permit|deny", "permit"),
             ("protocol", "Protocolo (avançada)",
              "choice:ip|tcp|udp|icmp|gre|ospf", "ip"),
             ("src", "Origem (IP ou 'any')", "text", "any"),
             ("src_w", "Curinga origem (ex.: 0.0.0.255)", "text", ""),
             ("dst", "Destino (avançada, IP ou 'any')", "text", "any"),
             ("dst_w", "Curinga destino", "text", ""),
             ("src_port", "Porta origem (0=qualquer)", "int", 0),
             ("dst_port", "Porta destino (0=qualquer)", "int", 0),
             ("rule_id", "ID da regra (0=automático)", "int", 0)],
            self)
        if not values:
            return
        if values["mode"] == "básica":
            cmds = translator.acl_rule(
                values["acl"], values["action"], values["src"],
                values["src_w"], rule_id=values["rule_id"])
        else:
            cmds = translator.acl_advanced_rule(
                values["acl"], values["action"], values["protocol"],
                values["src"], values["src_w"], values["dst"], values["dst_w"],
                src_port=values["src_port"] or None,
                dst_port=values["dst_port"] or None,
                rule_id=values["rule_id"] or None)
        self._apply(cmds, then=self.refresh_acl)

    def _acl_apply(self):
        values = SimpleForm.ask(
            "Aplicar ACL na interface (traffic-filter)",
            [("interface", "Interface", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound"),
             ("acl", "Número da ACL", "int", 3000),
             ("remove", "Remover o filtro", "bool", False)],
            self)
        if values and values["interface"]:
            self._apply(translator.traffic_filter(
                values["interface"], values["direction"], values["acl"],
                remove=values["remove"]))

    def _acl_del_rule(self):
        row = self._selected(self.acl_table, self._acl_rows)
        if row:
            self._apply(translator.acl_rule(
                int(row["acl"]), "", rule_id=int(row["rule"]), remove=True),
                then=self.refresh_acl)

    def _acl_del_acl(self):
        row = self._selected(self.acl_table, self._acl_rows)
        if not row:
            return
        if QMessageBox.question(
                self, "Remover ACL",
                f"Remover a ACL {row['acl']} inteira?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self._apply(translator.acl_delete(int(row["acl"])),
                        then=self.refresh_acl)

    # ================================================================== #
    # Aba Traffic-Policy (MQC)
    # ================================================================== #
    def _build_mqc_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        for text, slot in [("Atualizar", self.refresh_mqc),
                           ("Classificador...", self._mqc_classifier),
                           ("Comportamento...", self._mqc_behavior),
                           ("Política...", self._mqc_policy),
                           ("Aplicar...", self._mqc_apply),
                           ("Assistente CGNAT...", self._cgnat_wizard)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        rem = QPushButton("Remover")
        rem.setObjectName("dangerBtn")
        rem.clicked.connect(self._mqc_remove)
        bar.addWidget(rem)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.mqc_table = _table(["Tipo", "Nome", "Detalhe"])
        lay.addWidget(self.mqc_table)
        return w

    def refresh_mqc(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando traffic-policy...")

        def fetch():
            def safe(cmd):
                try:
                    return self.session.run(cmd, timeout=12.0)
                except Exception:
                    return ""
            cls = parsers.parse_mqc_blocks(
                safe("display traffic classifier user-defined"), "Classifier")
            beh = parsers.parse_mqc_blocks(
                safe("display traffic behavior user-defined"), "Behavior")
            pol = parsers.parse_mqc_blocks(
                safe("display traffic policy user-defined"), "Policy")
            rows = ([{"kind": "Classificador", **r} for r in cls]
                    + [{"kind": "Comportamento", **r} for r in beh]
                    + [{"kind": "Política", **r} for r in pol])
            return rows

        def done(rows):
            self._mqc_rows = rows
            self._fill(self.mqc_table,
                       [[r["kind"], r["name"], r.get("detail", "")]
                        for r in rows])
            self._set_busy(False, f"{len(rows)} objeto(s) MQC")

        run_async(fetch, done, self._fail)

    def _mqc_classifier(self):
        values = SimpleForm.ask(
            "Classificador de tráfego",
            [("name", "Nome", "text", ""),
             ("operator", "Operador", "choice:or|and", "or"),
             ("acl", "ACL a casar (0=não)", "int", 0),
             ("dscp", "DSCP a casar (vazio=não)", "text", "")],
            self)
        if values and values["name"]:
            self._apply(translator.fw_classifier(
                values["name"], values["operator"], values["acl"],
                values["dscp"]), then=self.refresh_mqc)

    def _mqc_behavior(self):
        values = SimpleForm.ask(
            "Comportamento de tráfego",
            [("name", "Nome", "text", ""),
             ("kind", "Ação",
              "choice:permit|deny|redirect-nexthop|redirect-interface|remark|car",
              "permit"),
             ("nexthop", "Next-hop (redirect-nexthop / CGNAT)", "text", ""),
             ("interface", "Interface (redirect-interface)", "text", ""),
             ("car_kbps", "CAR cir kbps (0=não)", "int", 0),
             ("dscp", "Remarcar DSCP (remark)", "text", "")],
            self)
        if values and values["name"]:
            self._apply(translator.fw_behavior(
                values["name"], values["kind"], values["nexthop"],
                values["interface"], values["car_kbps"], values["dscp"]),
                then=self.refresh_mqc)

    def _mqc_policy(self):
        values = SimpleForm.ask(
            "Política (associa classificador → comportamento)",
            [("policy", "Nome da política", "text", ""),
             ("classifier", "Classificador", "text", ""),
             ("behavior", "Comportamento", "text", ""),
             ("precedence", "Precedência (0=auto)", "int", 0),
             ("share_mode", "share-mode", "bool", False)],
            self)
        if values and values["policy"] and values["classifier"]:
            self._apply(translator.fw_policy_bind(
                values["policy"], values["classifier"], values["behavior"],
                values["precedence"], values["share_mode"]),
                then=self.refresh_mqc)

    def _mqc_apply(self):
        values = SimpleForm.ask(
            "Aplicar traffic-policy",
            [("scope", "Escopo", "choice:interface|global", "interface"),
             ("interface", "Interface (se escopo=interface)", "text", ""),
             ("policy", "Nome da política", "text", ""),
             ("direction", "Direção", "choice:inbound|outbound", "inbound"),
             ("remove", "Remover aplicação", "bool", False)],
            self)
        if not values or not values["policy"]:
            return
        if values["scope"] == "global":
            self._apply(translator.fw_policy_apply_global(
                values["policy"], values["direction"],
                remove=values["remove"]))
        elif values["interface"]:
            self._apply(translator.fw_policy_apply(
                values["interface"], values["policy"], values["direction"],
                remove=values["remove"]))

    def _cgnat_wizard(self):
        values = SimpleForm.ask(
            "Assistente CGNAT (redirecionamento por traffic-policy)",
            [("acl", "Número da ACL", "int", 3000),
             ("source", "Origem CGNAT (IP)", "text", "100.64.0.0"),
             ("wildcard", "Curinga", "text", "0.63.255.255"),
             ("classifier", "Classificador", "text", "C-CGNAT"),
             ("behavior", "Comportamento", "text", "B-CGNAT"),
             ("policy", "Política", "text", "P-CGNAT"),
             ("nexthop", "Next-hop do CGNAT (ex.: A10)", "text", ""),
             ("precedence", "Precedência", "int", 15),
             ("direction", "Direção (aplicação global)",
              "choice:inbound|outbound", "inbound"),
             ("apply_global", "Aplicar globalmente agora", "bool", True)],
            self)
        if values and values["nexthop"]:
            self._apply(translator.fw_cgnat_chain(
                values["acl"], values["source"], values["wildcard"],
                values["classifier"], values["behavior"], values["policy"],
                values["nexthop"], precedence=values["precedence"],
                apply_global=values["apply_global"],
                direction=values["direction"]), then=self.refresh_mqc)

    def _mqc_remove(self):
        row = self._selected(self.mqc_table, self._mqc_rows)
        if not row:
            return
        kind, name = row["kind"], row["name"]
        if kind == "Classificador":
            cmds = translator.fw_classifier(name, remove=True)
        elif kind == "Comportamento":
            cmds = translator.fw_behavior(name, remove=True)
        else:
            cmds = translator.fw_policy_delete(name)
        self._apply(cmds, then=self.refresh_mqc)

    # ================================================================== #
    # Aba NAT
    # ================================================================== #
    def _build_nat_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        for text, slot in [("Atualizar", self.refresh_nat),
                           ("NAT de saída...", self._nat_outbound),
                           ("Redirecionar porta...", self._nat_server)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        rem = QPushButton("Remover")
        rem.setObjectName("dangerBtn")
        rem.clicked.connect(self._nat_remove)
        bar.addWidget(rem)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.nat_table = _table(
            ["Tipo", "Interface", "ACL/Protocolo", "Global", "Interno"])
        lay.addWidget(self.nat_table)
        return w

    def refresh_nat(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando NAT...")

        def fetch():
            rows = []
            for r in parsers.parse_nat_outbound(
                    self.session.run("display nat outbound", timeout=15.0)):
                rows.append({"kind": "outbound", "interface": r["interface"],
                             "info": r["acl"], "global": r["target"],
                             "inside": r["type"], "raw": r})
            for r in parsers.parse_nat_servers(
                    self.session.run("display nat server", timeout=15.0)):
                rows.append({"kind": "server", "interface": r["interface"],
                             "info": r["protocol"], "global": r["global"],
                             "inside": r["inside"], "raw": r})
            return rows

        def done(rows):
            self._nat_rows = rows
            self._fill(self.nat_table, [
                ["Saída (easy-ip)" if r["kind"] == "outbound"
                 else "Porta (server)",
                 r["interface"], r["info"], r["global"], r["inside"]]
                for r in rows])
            self._set_busy(False, f"{len(rows)} regra(s) de NAT")

        run_async(fetch, done, self._fail)

    def _nat_outbound(self):
        values = SimpleForm.ask(
            "NAT de saída (easy-ip / PAT por ACL)",
            [("interface", "Interface de saída (WAN)", "text", ""),
             ("acl", "ACL com as redes internas", "int", 2000),
             ("remove", "Remover", "bool", False)],
            self)
        if values and values["interface"]:
            self._apply(translator.nat_outbound(
                values["interface"], values["acl"], remove=values["remove"]),
                then=self.refresh_nat)

    def _nat_server(self):
        values = SimpleForm.ask(
            "Redirecionamento de porta (NAT server)",
            [("interface", "Interface de entrada (WAN)", "text", ""),
             ("protocol", "Protocolo", "choice:tcp|udp", "tcp"),
             ("global_ip", "IP global (vazio = IP da interface)", "text", ""),
             ("global_port", "Porta externa", "int", 8080),
             ("inside_ip", "IP interno", "text", ""),
             ("inside_port", "Porta interna", "int", 80)],
            self)
        if values and values["interface"] and values["inside_ip"]:
            self._apply(translator.nat_server(
                values["interface"], values["protocol"], values["global_ip"],
                values["global_port"], values["inside_ip"],
                values["inside_port"]), then=self.refresh_nat)

    def _nat_remove(self):
        row = self._selected(self.nat_table, self._nat_rows)
        if not row:
            return
        raw = row["raw"]
        if row["kind"] == "outbound":
            self._apply(translator.nat_outbound(
                row["interface"], int(raw["acl"]), remove=True),
                then=self.refresh_nat)
            return
        g_ip, _, g_port = raw["global"].rpartition("/")
        i_ip, _, i_port = raw["inside"].rpartition("/")
        if g_ip == "current-interface":
            g_ip = ""
        self._apply(translator.nat_server(
            row["interface"], raw["protocol"] or "tcp", g_ip,
            int(g_port or 0), i_ip, int(i_port or 0), remove=True),
            then=self.refresh_nat)

    # ================================================================== #
    # Infra
    # ================================================================== #
    def _apply(self, commands: list[str], then=None):
        two_stage = getattr(self.session, "two_stage", False)
        if not CommandPreviewDialog.confirm(commands, self,
                                            two_stage=two_stage):
            return
        self._set_busy(True, "Enviando configuração...")

        def send():
            return self.session.configure(commands)

        def done(output: str):
            self._set_busy(False, "Configuração aplicada")
            if "Error" in output or "error" in output:
                QMessageBox.warning(
                    self, self.title,
                    "O equipamento reportou um problema:\n\n" + output)
            if then:
                then()

        run_async(send, done, self._fail)

    def _filter(self, table: QTableWidget, edit: QLineEdit):
        text = edit.text().lower().strip()
        for r in range(table.rowCount()):
            match = not text or any(
                (table.item(r, c) and text in table.item(r, c).text().lower())
                for c in range(table.columnCount()))
            table.setRowHidden(r, not match)

    def _fail(self, message: str):
        self._set_busy(False, "")
        QMessageBox.warning(self, self.title, message)

    def _selected(self, table: QTableWidget, rows: list[dict]) -> dict | None:
        items = table.selectedItems()
        if not items:
            QMessageBox.information(self, self.title,
                                    "Selecione uma linha na tabela.")
            return None
        idx = table.row(items[0])
        return rows[idx] if 0 <= idx < len(rows) else None

    @staticmethod
    def _fill(table: QTableWidget, data: list[list[str]]):
        table.setRowCount(0)
        for row_data in data:
            r = table.rowCount()
            table.insertRow(r)
            for c, value in enumerate(row_data):
                table.setItem(r, c, QTableWidgetItem(str(value)))

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.status_lbl.setText(text)
