"""Painel PPPoE / BAS — equivalente à janela *PPP* do WinBox, para os
roteadores de borda de banda larga (BRAS) Huawei NE8000.

Abas:

* **Usuários online** — assinantes PPPoE/IPoE conectados
  (``display access-user``), com filtro por domínio/interface e botão para
  desconectar (``cut access-user``).
* **Domínios** — domínios AAA (``display domain``), que funcionam como os
  *profiles* do WinBox (definem pool de IP, esquemas de auth/accounting e
  grupo RADIUS). Criar/remover.
* **Pools de assinante** — pools ``ip pool ... bas local``
  (``display ip pool``). Criar/remover.
* **PPPoE Server** — cria o ``Virtual-Template`` servidor PPPoE e habilita
  o acesso BAS (``bas access-type layer2-subscriber``) numa sub-interface.

Como sempre, toda alteração passa pela pré-visualização do CLI antes de ir
ao equipamento por SSH.
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


class PppoePanel(QWidget):
    title = "PPPoE / BAS"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._user_rows: list[dict] = []
        self._domain_rows: list[dict] = []
        self._pool_rows: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel(
            "<b>Banda larga (BRAS)</b> — assinantes PPPoE/IPoE, domínios e "
            "pools. Equivale ao PPP do WinBox."))
        top.addStretch(1)
        self.status_lbl = QLabel("")
        top.addWidget(self.status_lbl)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_users_tab(), "Usuários online")
        self.tabs.addTab(self._build_domains_tab(), "Domínios")
        self.tabs.addTab(self._build_pools_tab(), "Pools de assinante")
        self.tabs.addTab(self._build_server_tab(), "PPPoE Server")
        root.addWidget(self.tabs)

        # Domínios e pools são baratos — carrega de cara (num único job, para
        # não colidir no guard _busy). Usuários online podem ser milhares;
        # carrega só quando o usuário pedir.
        self._initial_load()

    def _initial_load(self):
        self._set_busy(True, "Consultando domínios e pools...")

        def fetch():
            domains = parsers.parse_domains(
                self.session.run("display domain", timeout=15.0))
            pools = parsers.parse_ip_pools(
                self.session.run("display ip pool", timeout=20.0))
            return domains, pools

        def done(data):
            domains, pools = data
            self._domain_rows = domains
            self._fill(self.domain_table,
                       [[r["name"], r["state"] or "—"] for r in domains])
            self._pool_rows = pools
            self._fill(self.pool_table,
                       [[r["name"], r["gateway"], r["network"],
                         r["used"], r["idle"]] for r in pools])
            self._set_busy(False,
                           f"{len(domains)} domínio(s), {len(pools)} pool(s)")

        run_async(fetch, done, self._fail)

    # ================================================================== #
    # Aba: Usuários online
    # ================================================================== #
    def _build_users_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        bar = QHBoxLayout()
        self.load_users_btn = QPushButton("Carregar / Atualizar")
        self.load_users_btn.clicked.connect(self.refresh_users)
        bar.addWidget(self.load_users_btn)
        bar.addWidget(QLabel("Domínio:"))
        self.user_domain = QLineEdit()
        self.user_domain.setPlaceholderText("(opcional)")
        self.user_domain.setMaximumWidth(160)
        bar.addWidget(self.user_domain)
        bar.addWidget(QLabel("Interface:"))
        self.user_iface = QLineEdit()
        self.user_iface.setPlaceholderText("(opcional)")
        self.user_iface.setMaximumWidth(160)
        bar.addWidget(self.user_iface)
        bar.addStretch(1)
        self.disconnect_btn = QPushButton("Desconectar usuário")
        self.disconnect_btn.setObjectName("dangerBtn")
        self.disconnect_btn.clicked.connect(self._disconnect_user)
        bar.addWidget(self.disconnect_btn)
        lay.addLayout(bar)

        find_bar = QHBoxLayout()
        find_bar.addStretch(1)
        find_bar.addWidget(QLabel("Filtrar:"))
        self.user_find = QLineEdit()
        self.user_find.setObjectName("findBox")
        self.user_find.setPlaceholderText("Find")
        self.user_find.setClearButtonEnabled(True)
        self.user_find.textChanged.connect(self._filter_users)
        find_bar.addWidget(self.user_find)
        self.user_count = QLabel("")
        self.user_count.setObjectName("itemCount")
        find_bar.addWidget(self.user_count)
        lay.addLayout(find_bar)

        self.user_table = _table(
            ["UserID", "Usuário", "IP", "MAC", "Interface", "Domínio"])
        self.user_table.setSelectionMode(QTableWidget.ExtendedSelection)
        lay.addWidget(self.user_table)
        return w

    def refresh_users(self):
        if self._busy:
            return
        cmd = "display access-user"
        dom = self.user_domain.text().strip()
        iface = self.user_iface.text().strip()
        if dom:
            cmd += f" domain {dom}"
        elif iface:
            cmd += f" interface {iface}"
        self._set_busy(True, "Consultando assinantes online...")

        def fetch():
            return parsers.parse_access_users(
                self.session.run(cmd, timeout=60.0))

        def done(rows):
            self._user_rows = rows
            self._fill(self.user_table,
                       [[r["userid"], r["username"], r["ip"], r["mac"],
                         r["interface"], r["domain"]] for r in rows])
            self._filter_users()
            self._set_busy(False, f"{len(rows)} assinante(s) online")

        run_async(fetch, done, self._fail)

    def _filter_users(self):
        text = self.user_find.text().lower().strip()
        total = self.user_table.rowCount()
        shown = 0
        for r in range(total):
            match = not text or any(
                (self.user_table.item(r, c) and
                 text in self.user_table.item(r, c).text().lower())
                for c in range(self.user_table.columnCount()))
            self.user_table.setRowHidden(r, not match)
            shown += int(match)
        self.user_count.setText(
            f"{total} online" if shown == total else f"{shown} de {total}")

    def _disconnect_user(self):
        items = self.user_table.selectedItems()
        if not items:
            QMessageBox.information(
                self, self.title, "Selecione um ou mais usuários na tabela.")
            return
        rows = sorted({self.user_table.row(it) for it in items})
        names = [self._user_rows[r]["username"] for r in rows
                 if r < len(self._user_rows) and self._user_rows[r]["username"]]
        if not names:
            return
        cmds = [translator.cut_access_user("username", n)[0] for n in names]
        # cut access-user é operacional (system-view, sem commit).
        if not CommandPreviewDialog.confirm(cmds, self, system_view=True):
            return
        self._set_busy(True, "Desconectando...")

        def send():
            return self.session.run_many(["system-view", *cmds, "return"])

        def done(_out):
            self._set_busy(False, "Comando enviado")
            self.refresh_users()

        run_async(send, done, self._fail)

    # ================================================================== #
    # Aba: Domínios
    # ================================================================== #
    def _build_domains_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        b = QPushButton("Atualizar")
        b.clicked.connect(self.refresh_domains)
        bar.addWidget(b)
        add = QPushButton("Criar domínio...")
        add.clicked.connect(self._add_domain)
        bar.addWidget(add)
        rem = QPushButton("Remover")
        rem.setObjectName("dangerBtn")
        rem.clicked.connect(self._remove_domain)
        bar.addWidget(rem)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.domain_table = _table(["Domínio", "Estado"])
        lay.addWidget(self.domain_table)
        return w

    def refresh_domains(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando domínios...")

        def fetch():
            return parsers.parse_domains(
                self.session.run("display domain", timeout=15.0))

        def done(rows):
            self._domain_rows = rows
            self._fill(self.domain_table,
                       [[r["name"], r["state"] or "—"] for r in rows])
            self._set_busy(False, f"{len(rows)} domínio(s)")

        run_async(fetch, done, self._fail)

    def _add_domain(self):
        values = SimpleForm.ask(
            "Criar / editar domínio (profile do assinante)",
            [("name", "Nome do domínio", "text", ""),
             ("auth", "Authentication-scheme", "text", ""),
             ("acct", "Accounting-scheme", "text", ""),
             ("group", "Grupo RADIUS", "text", ""),
             ("pool", "IP-pool", "text", ""),
             ("pool6", "IPv6-pool (opcional)", "text", "")],
            self)
        if values and values["name"]:
            self._apply(translator.bras_domain(
                values["name"], values["auth"], values["acct"],
                values["group"], values["pool"], values["pool6"]),
                then=self.refresh_domains)

    def _remove_domain(self):
        row = self._selected(self.domain_table, self._domain_rows)
        if not row:
            return
        if QMessageBox.question(
                self, "Remover domínio",
                f"Remover o domínio '{row['name']}'? Assinantes que o usam "
                f"podem cair.",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self._apply(translator.bras_domain(row["name"], remove=True),
                        then=self.refresh_domains)

    # ================================================================== #
    # Aba: Pools de assinante
    # ================================================================== #
    def _build_pools_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        b = QPushButton("Atualizar")
        b.clicked.connect(self.refresh_pools)
        bar.addWidget(b)
        add = QPushButton("Criar pool...")
        add.clicked.connect(self._add_pool)
        bar.addWidget(add)
        rem = QPushButton("Remover")
        rem.setObjectName("dangerBtn")
        rem.clicked.connect(self._remove_pool)
        bar.addWidget(rem)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.pool_table = _table(
            ["Pool", "Gateway", "Rede", "Usados", "Livres"])
        lay.addWidget(self.pool_table)
        return w

    def refresh_pools(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando pools...")

        def fetch():
            return parsers.parse_ip_pools(
                self.session.run("display ip pool", timeout=20.0))

        def done(rows):
            self._pool_rows = rows
            self._fill(self.pool_table,
                       [[r["name"], r["gateway"], r["network"],
                         r["used"], r["idle"]] for r in rows])
            self._set_busy(False, f"{len(rows)} pool(s)")

        run_async(fetch, done, self._fail)

    def _add_pool(self):
        values = SimpleForm.ask(
            "Criar pool de assinante (bas local)",
            [("name", "Nome do pool", "text", ""),
             ("gateway", "Gateway", "text", ""),
             ("mask", "Máscara (ex.: 255.255.252.0)", "text", "255.255.252.0"),
             ("start", "IP inicial", "text", ""),
             ("end", "IP final", "text", ""),
             ("dns", "DNS (opcional)", "text", "")],
            self)
        if values and values["name"] and values["gateway"] and values["start"]:
            self._apply(translator.bras_ip_pool(
                values["name"], values["gateway"], values["mask"],
                values["start"], values["end"], values["dns"]),
                then=self.refresh_pools)

    def _remove_pool(self):
        row = self._selected(self.pool_table, self._pool_rows)
        if row:
            self._apply(translator.bras_ip_pool(
                row["name"], "", "", "", "", remove=True),
                then=self.refresh_pools)

    # ================================================================== #
    # Aba: PPPoE Server
    # ================================================================== #
    def _build_server_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "Crie o servidor PPPoE (Virtual-Template) e habilite o acesso "
            "BAS na sub-interface que recebe os assinantes."))
        bar = QHBoxLayout()
        vt = QPushButton("Criar Virtual-Template (PPPoE)...")
        vt.clicked.connect(self._add_template)
        bar.addWidget(vt)
        bas = QPushButton("Habilitar acesso BAS na interface...")
        bas.clicked.connect(self._add_bas_interface)
        bar.addWidget(bas)
        bar.addStretch(1)
        lay.addLayout(bar)

        bar2 = QHBoxLayout()
        user = QPushButton("Conta PPPoE local...")
        user.clicked.connect(self._add_local_user)
        bar2.addWidget(user)
        bar2.addStretch(1)
        lay.addLayout(bar2)
        lay.addStretch(1)
        return w

    def _add_template(self):
        values = SimpleForm.ask(
            "Virtual-Template servidor PPPoE",
            [("id", "Número do Virtual-Template", "int", 0),
             ("ac", "AC-name (ex.: NAS-USN)", "text", ""),
             ("svc", "Service-name (opcional)", "text", ""),
             ("auth", "Autenticação", "choice:pap|chap|chap pap", "pap"),
             ("remove", "Remover este Virtual-Template", "bool", False)],
            self)
        if values is not None:
            self._apply(translator.pppoe_server_template(
                values["id"], values["ac"], values["svc"],
                values["auth"], remove=values["remove"]))

    def _add_bas_interface(self):
        values = SimpleForm.ask(
            "Habilitar acesso BAS (PPPoE/IPoE) na interface",
            [("interface", "Interface (ex.: GigabitEthernet0/5/0.1001)",
              "text", ""),
             ("vlan", "User-VLAN (ex.: 1001)", "text", ""),
             ("domain", "Domínio padrão de autenticação", "text", ""),
             ("remove", "Remover BAS desta interface", "bool", False)],
            self)
        if values and values["interface"]:
            self._apply(translator.bras_access_interface(
                values["interface"], values["vlan"], values["domain"],
                remove=values["remove"]))

    def _add_local_user(self):
        values = SimpleForm.ask(
            "Conta PPPoE local (Secrets)",
            [("user", "Usuário", "text", ""),
             ("password", "Senha (irreversible-cipher)", "text", ""),
             ("remove", "Remover conta", "bool", False)],
            self)
        if values and values["user"]:
            self._apply(translator.local_ppp_user(
                values["user"], values["password"],
                remove=values["remove"]))

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
