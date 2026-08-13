"""Painel Usuários — contas locais e configuração de serviços AAA."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QMessageBox

from ...core import parsers, translator
from ..forms import SimpleForm
from .base import BasePanel


class UsersPanel(BasePanel):
    title = "Usuários"
    columns = ["Usuário", "Estado", "Serviços"]

    def build_toolbar(self, layout):
        for text, slot in [
            ("Novo usuário...", self._add),
            ("Alterar senha...", self._change_password),
            ("Alterar privilégio...", self._change_privilege),
            ("Configurar SSH...", self._ssh_user),
            ("AAA/Autenticação...", self._aaa),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
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
        selected = self.require_selection("um ou mais usuários")
        if not selected:
            return
        # O usuário da sessão fica de fora mesmo numa seleção em massa:
        # removê-lo derrubaria o próprio acesso no meio da operação.
        rows = [r for r in selected if r["username"] != self.session.username]
        if len(rows) != len(selected):
            QMessageBox.warning(
                self, self.title,
                f"O usuário “{self.session.username}” é o da sessão atual e "
                "foi retirado da seleção — removê-lo derrubaria o seu "
                "próprio acesso.")
        if not rows:
            return
        if len(rows) > 1 and QMessageBox.question(
                self, self.title,
                f"Remover {len(rows)} usuários?\n\n"
                + ", ".join(r["username"] for r in rows[:10]),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.apply_for_each(
            rows, lambda r: translator.local_user_delete(r["username"]))

    def _change_password(self):
        row = self.selected_row()
        username = row["username"] if row else ""
        values = SimpleForm.ask(
            "Alterar senha de usuário",
            [("username", "Nome de usuário", "text", username),
             ("new_password", "Nova senha", "text", "")],
            self)
        if values and values["username"] and values["new_password"]:
            cmds = [
                "aaa",
                f"local-user {values['username']} password irreversible-cipher "
                f"{values['new_password']}",
            ]
            self.apply_commands(cmds)

    def _change_privilege(self):
        row = self.selected_row()
        username = row["username"] if row else ""
        values = SimpleForm.ask(
            "Alterar nível de privilégio",
            [("username", "Nome de usuário", "text", username),
             ("privilege", "Nível de privilégio (0-15)", "int", 15)],
            self)
        if values and values["username"]:
            cmds = [
                "aaa",
                f"local-user {values['username']} privilege level "
                f"{values['privilege']}",
            ]
            self.apply_commands(cmds)

    def _ssh_user(self):
        row = self.selected_row()
        username = row["username"] if row else ""
        values = SimpleForm.ask(
            "Configuração SSH para usuário",
            [("username", "Nome de usuário", "text", username),
             ("auth_type",
              "Tipo de autenticação SSH",
              "choice:password|rsa|dsa|ecc|password-rsa|all",
              "password"),
             ("service_type",
              "Tipo de serviço",
              "choice:stelnet|sftp|scp|all",
              "stelnet")],
            self)
        if values and values["username"]:
            self.apply_commands(translator.ssh_user(
                values["username"], values["auth_type"],
                values["service_type"]))

    def _aaa(self):
        values = SimpleForm.ask(
            "Esquema de autenticação AAA",
            [("scheme",
              "Autenticação padrão",
              "choice:local|radius|tacacs|local-radius|radius-local",
              "local")],
            self)
        if values:
            self.apply_commands(
                translator.aaa_authentication_scheme(values["scheme"]))
