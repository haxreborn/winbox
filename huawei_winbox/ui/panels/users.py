"""Painel Usuários — contas locais de gerenciamento (AAA)."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class UsersPanel(BasePanel):
    title = "Usuários"
    columns = ["Usuário", "Estado", "Serviços"]

    def build_toolbar(self, layout):
        add_btn = QPushButton("Novo usuário...")
        add_btn.clicked.connect(self._add)
        layout.addWidget(add_btn)
        del_btn = QPushButton("Remover")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._remove)
        layout.addWidget(del_btn)

    def fetch(self):
        text = self.session.run("display aaa local-user")
        if "Error" in text or not text.strip():
            text = self.session.run("display local-user")
        return parsers.parse_local_users(text)

    def row_values(self, row):
        return [row["username"], row["state"], row["type"]]

    def _add(self):
        values = SimpleForm.ask(
            "Novo usuário local",
            [("username", "Nome de usuário", "text", ""),
             ("password", "Senha", "text", ""),
             ("privilege", "Nível de privilégio (0-15)", "int", 15),
             ("services",
              "Serviços",
              "choice:ssh|telnet|ssh telnet|web|terminal|ssh terminal|all",
              "ssh")],
            self)
        if values and values["username"] and values["password"]:
            self.apply_commands(translator.local_user(
                values["username"], values["password"],
                values["privilege"], values["services"]))

    def _remove(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, self.title, "Selecione um usuário.")
            return
        if row["username"] == self.session.username:
            QMessageBox.warning(
                self, self.title,
                "Este é o usuário da sessão atual — removê-lo derrubaria "
                "o seu próprio acesso.")
            return
        self.apply_commands(translator.local_user_delete(row["username"]))
