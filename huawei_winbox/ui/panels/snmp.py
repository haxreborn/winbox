"""Painel SNMP — configuração e monitoramento do agente SNMP do VRP.

Permite habilitar/desabilitar o SNMP, gerenciar comunidades (read/write),
configurar destinos de trap, e definir contact/location do sistema. Toda
configuração passa pela pré-visualização de CLI antes do envio via SSH.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton,
    QLabel, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QGridLayout,
)

from ...core import parsers, translator
from ..command_preview import CommandPreviewDialog
from ..forms import SimpleForm
from ..worker import run_async


def _table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    # Seleção múltipla: desconectar dez assinantes ou apagar dez
    # regras um a um é trabalho repetido. Ctrl+clique, Shift+clique
    # e Ctrl+A funcionam como em qualquer lista.
    t.setSelectionMode(QTableWidget.ExtendedSelection)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


class SnmpPanel(QWidget):
    title = "SNMP"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # Barra superior
        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        self.status_lbl = QLabel("")
        top.addWidget(self.status_lbl)
        top.addStretch(1)
        self.enable_btn = QPushButton("Habilitar SNMP...")
        self.enable_btn.clicked.connect(self._enable)
        top.addWidget(self.enable_btn)
        self.disable_btn = QPushButton("Desabilitar SNMP")
        self.disable_btn.setObjectName("dangerBtn")
        self.disable_btn.clicked.connect(self._disable)
        top.addWidget(self.disable_btn)
        root.addLayout(top)

        # Informações gerais
        self.info_box = QGroupBox("Informações do agente SNMP")
        info_grid = QGridLayout(self.info_box)
        self._info_labels = {}
        for row_idx, key in enumerate(("version", "contact", "location")):
            lbl = {"version": "Versão", "contact": "Contato",
                   "location": "Localização"}[key]
            info_grid.addWidget(QLabel(f"<b>{lbl}:</b>"), row_idx, 0)
            val = QLabel("—")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_grid.addWidget(val, row_idx, 1)
            self._info_labels[key] = val
        # Botões de edição
        for row_idx, (key, label, fn) in enumerate([
            ("contact", "Editar contato...", self._set_contact),
            ("location", "Editar localização...", self._set_location),
        ], start=0):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            info_grid.addWidget(btn, row_idx + 1, 2)
        root.addWidget(self.info_box)

        # Tabs: Comunidades | Trap Hosts
        tabs = QTabWidget()

        # Tab Comunidades
        comm_widget = QWidget()
        comm_layout = QVBoxLayout(comm_widget)
        comm_bar = QHBoxLayout()
        add_comm = QPushButton("Adicionar comunidade...")
        add_comm.clicked.connect(self._add_community)
        comm_bar.addWidget(add_comm)
        del_comm = QPushButton("Remover")
        del_comm.setObjectName("dangerBtn")
        del_comm.clicked.connect(self._remove_community)
        comm_bar.addWidget(del_comm)
        comm_bar.addStretch(1)
        comm_layout.addLayout(comm_bar)
        self.comm_table = _table(["Comunidade", "Acesso"])
        comm_layout.addWidget(self.comm_table)
        tabs.addTab(comm_widget, "Comunidades")

        # Tab Trap Hosts
        trap_widget = QWidget()
        trap_layout = QVBoxLayout(trap_widget)
        trap_bar = QHBoxLayout()
        add_trap = QPushButton("Adicionar destino de trap...")
        add_trap.clicked.connect(self._add_trap)
        trap_bar.addWidget(add_trap)
        del_trap = QPushButton("Remover")
        del_trap.setObjectName("dangerBtn")
        del_trap.clicked.connect(self._remove_trap)
        trap_bar.addWidget(del_trap)
        trap_bar.addStretch(1)
        trap_layout.addLayout(trap_bar)
        self.trap_table = _table(["Endereço IP", "Comunidade", "Versão"])
        trap_layout.addWidget(self.trap_table)
        tabs.addTab(trap_widget, "Destinos de Trap")

        root.addWidget(tabs)
        self.refresh()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe(session, cmd: str) -> str:
        """Roda um comando com timeout curto (8 s); retorna "" se falhar.

        Comandos SNMP devem responder em < 2 s. O timeout curto garante
        que uma sintaxe inválida (que deixaria o VRP aguardando mais input)
        não bloqueie o lock da sessão por mais de 8 s.
        """
        try:
            return session.run(cmd, timeout=8.0)
        except Exception:
            return ""

    def refresh(self):
        if self._busy:
            return
        self._set_busy(True, "Consultando agente SNMP...")

        def fetch():
            # sys-info: sempre válido, mostra versão, contact, location.
            sys_text = self._safe(self.session, "display snmp-agent sys-info")
            sys_info = parsers.parse_snmp_sys_info(sys_text)

            # Comunidades: VRP aceita "community read" e "community write"
            # separados; "community" sem sufixo pode não existir em todas
            # as versões e deixar o shell aguardando.
            comm_r = self._safe(self.session, "display snmp-agent community read")
            comm_w = self._safe(self.session, "display snmp-agent community write")
            communities = (parsers.parse_snmp_communities(comm_r)
                           + parsers.parse_snmp_communities(comm_w))

            # Trap hosts: tenta as duas grafias usadas em versões diferentes.
            trap_text = self._safe(
                self.session, "display snmp-agent target-host")
            if not trap_text.strip():
                trap_text = self._safe(
                    self.session, "display snmp-agent trap host")
            traps = parsers.parse_snmp_trap_hosts(trap_text)

            return sys_info, communities, traps

        def done(data):
            sys_info, communities, traps = data
            for key, val in sys_info.items():
                if key in self._info_labels:
                    self._info_labels[key].setText(val or "—")
            self._fill_table(self.comm_table,
                             [[r["name"], r["access"]] for r in communities])
            self._fill_table(self.trap_table,
                             [[r["address"], r["community"], r["version"]]
                              for r in traps])
            self._set_busy(False,
                           f"{len(communities)} comunidade(s), "
                           f"{len(traps)} trap host(s)")
            self._comm_rows = communities
            self._trap_rows = traps

        def fail(message: str):
            self.status_lbl.setText(f"Erro: {message}")
            self._set_busy(False, "")

        self._comm_rows = []
        self._trap_rows = []
        run_async(fetch, done, fail)

    # ------------------------------------------------------------------ #
    def _apply(self, commands: list[str]):
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
                QMessageBox.warning(self, self.title,
                                    "O equipamento reportou um problema:\n\n" + output)
            self.refresh()

        def fail(msg: str):
            self._set_busy(False, "")
            QMessageBox.warning(self, self.title, msg)

        run_async(send, done, fail)

    def _enable(self):
        values = SimpleForm.ask(
            "Habilitar agente SNMP",
            [("version",
              "Versão",
              "choice:v2c|v3|all|v1 v2c|v1",
              "v2c")],
            self)
        if values:
            self._apply(translator.snmp_enable(values["version"]))

    def _disable(self):
        if QMessageBox.question(
                self, "Desabilitar SNMP",
                "Isso removerá o agente SNMP inteiramente. Continuar?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self._apply(translator.snmp_disable())

    def _set_contact(self):
        current = self._info_labels["contact"].text()
        values = SimpleForm.ask(
            "Contato do sistema (SysContact)",
            [("contact", "Contato", "text", "" if current == "—" else current)],
            self)
        if values and values["contact"]:
            self._apply(translator.snmp_set_contact(values["contact"]))

    def _set_location(self):
        current = self._info_labels["location"].text()
        values = SimpleForm.ask(
            "Localização do sistema (SysLocation)",
            [("location", "Localização", "text", "" if current == "—" else current)],
            self)
        if values and values["location"]:
            self._apply(translator.snmp_set_location(values["location"]))

    def _add_community(self):
        values = SimpleForm.ask(
            "Nova comunidade SNMP",
            [("community", "Nome da comunidade", "text", ""),
             ("write", "Permissão de escrita (write)", "bool", False)],
            self)
        if values and values["community"]:
            self._apply(translator.snmp_add_community(
                values["community"], write=values["write"]))

    def _remove_community(self):
        rows = self._selected_many(self.comm_table, self._comm_rows)
        if not rows:
            return
        commands = []
        for row in rows:
            commands += translator.snmp_add_community(
                row["name"], write=(row["access"] == "write"), remove=True)
        self._apply(commands)

    def _add_trap(self):
        values = SimpleForm.ask(
            "Destino de trap SNMP",
            [("ip", "Endereço IP do gerenciador", "text", ""),
             ("community", "Comunidade (securityname)", "text", ""),
             ("udp_port", "Porta UDP (padrão 162)", "int", 162),
             ("version", "Versão SNMP", "choice:v2c|v1|v3", "v2c")],
            self)
        if values and values["ip"] and values["community"]:
            self._apply(translator.snmp_add_trap_host(
                values["ip"], values["community"],
                values["udp_port"], values["version"]))

    def _remove_trap(self):
        rows = self._selected_many(self.trap_table, self._trap_rows)
        if not rows:
            return
        commands = []
        for row in rows:
            commands += translator.snmp_add_trap_host(
                row["address"], row["community"], remove=True)
        self._apply(commands)

    # ------------------------------------------------------------------ #
    def _selected_many(self, table: QTableWidget,
                       rows: list[dict]) -> list[dict]:
        """Todas as linhas selecionadas (a seleção da tabela é múltipla).

        ``selectedItems()`` devolve células; a mesma linha volta uma vez
        por coluna, então é preciso reduzir por índice de linha.
        """
        indexes = sorted({table.row(item) for item in table.selectedItems()})
        chosen = [rows[i] for i in indexes if 0 <= i < len(rows)]
        if not chosen:
            QMessageBox.information(
                self, self.title,
                "Selecione uma ou mais linhas na tabela.\n\n"
                "Ctrl+clique escolhe avulsas, Shift+clique escolhe uma "
                "faixa e Ctrl+A escolhe todas.")
        return chosen

    def _selected_row(self, table: QTableWidget, rows: list[dict]) -> dict | None:
        items = table.selectedItems()
        if not items:
            QMessageBox.information(self, self.title,
                                    "Selecione uma linha na tabela.")
            return None
        idx = table.row(items[0])
        return rows[idx] if idx < len(rows) else None

    @staticmethod
    def _fill_table(table: QTableWidget, data: list[list[str]]):
        table.setRowCount(0)
        for row_data in data:
            r = table.rowCount()
            table.insertRow(r)
            for c, value in enumerate(row_data):
                table.setItem(r, c, QTableWidgetItem(str(value)))

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.refresh_btn.setEnabled(not busy)
        self.status_lbl.setText(text)
