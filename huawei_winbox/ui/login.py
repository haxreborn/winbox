"""Janela de conexão — réplica fiel da tela inicial do WinBox.

Barra de menus (File / Tools), formulário *Connect To / Login / Password*
com a caixa de seleção *Keep Password* à direita, a fileira de botões
(Add/Set · Connect) e, embaixo, a lista *Managed* com a barra de
ferramentas (filtro, Set Master Password, Find, all) e os equipamentos
salvos.

Quando o equipamento EXIGE a troca da senha no primeiro acesso (e recusar
com "N" derruba a sessão), um modal pede a nova senha e a conexão é
refeita aplicando a troca — sem poluir o formulário principal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QMenuBar, QToolButton, QComboBox, QFrame,
    QDialog, QApplication,
)

from ..core.profiles import (
    DeviceProfile, load_profiles, upsert_profile, remove_profile,
)
from ..core.session import HuaweiSession
from . import icons
from .worker import run_async


class PasswordChangeDialog(QDialog):
    """Modal pedido quando o equipamento força a troca de senha no login."""

    def __init__(self, host: str, username: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Troca de senha obrigatória")
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        msg = QLabel(
            f"O equipamento <b>{host}</b> exige a troca da senha do "
            f"usuário <b>{username}</b> no primeiro acesso.<br><br>"
            "Informe a nova senha para concluir o login.")
        msg.setWordWrap(True)
        lay.addWidget(msg)

        form = QGridLayout()
        form.addWidget(QLabel("Nova senha:"), 0, 0)
        self.new1 = QLineEdit()
        self.new1.setEchoMode(QLineEdit.Password)
        form.addWidget(self.new1, 0, 1)
        form.addWidget(QLabel("Confirmar:"), 1, 0)
        self.new2 = QLineEdit()
        self.new2.setEchoMode(QLineEdit.Password)
        self.new2.returnPressed.connect(self._accept)
        form.addWidget(self.new2, 1, 1)
        lay.addLayout(form)

        self.err = QLabel("")
        self.err.setStyleSheet("color:#c0392b;")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Trocar e conectar")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _accept(self):
        p1, p2 = self.new1.text(), self.new2.text()
        if not p1:
            self.err.setText("Informe a nova senha.")
            return
        if p1 != p2:
            self.err.setText("As senhas não conferem.")
            return
        self.accept()

    def new_password(self) -> str:
        return self.new1.text()


class LoginWindow(QWidget):
    connected = Signal(object)  # emite a HuaweiSession pronta

    def __init__(self):
        super().__init__()
        self.setObjectName("loginRoot")
        self.setWindowTitle("HuaweiBox — Conectar")
        self.resize(580, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setMenuBar(self._build_menubar())

        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(8)
        root.addLayout(body)

        body.addLayout(self._build_form())
        body.addWidget(self._hline())
        body.addWidget(self._build_tabs(), 1)

        self.status = QLabel("")
        self.status.setObjectName("loginStatusBar")
        self.status.setStyleSheet(
            "background:#e4e4e4; border-top:1px solid #b8b8b8; padding:3px 8px;")
        root.addWidget(self.status)

        self._load_table()

    # ================================================================== #
    # Construção da casca (estilo WinBox)
    # ================================================================== #
    def _build_menubar(self) -> QMenuBar:
        bar = QMenuBar()
        m_file = bar.addMenu("File")
        m_file.addAction("Conectar", self._connect_clicked)
        m_file.addAction("Adicionar/Salvar", self._add_set)
        m_file.addSeparator()
        m_file.addAction("Sair", QApplication.quit)

        m_tools = bar.addMenu("Tools")
        m_tools.addAction("Remover selecionado", self._forget)
        m_tools.addAction("Atualizar lista", self._load_table)
        return bar

    def _build_form(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(self._rlabel("Connect To:"), 0, 0)
        self.host = QLineEdit()
        self.host.setPlaceholderText("endereço IP[:porta]  (ex.: 10.0.0.1:22)")
        self.host.returnPressed.connect(self._connect_clicked)
        grid.addWidget(self.host, 0, 1)
        self.keep_pwd = QCheckBox("Keep Password")
        self.keep_pwd.setChecked(True)
        self.keep_pwd.setToolTip("Lembrar a senha na lista de equipamentos.")
        grid.addWidget(self.keep_pwd, 0, 2)

        grid.addWidget(self._rlabel("Login:"), 1, 0)
        self.username = QLineEdit()
        grid.addWidget(self.username, 1, 1)

        grid.addWidget(self._rlabel("Password:"), 2, 0)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._connect_clicked)
        grid.addWidget(self.password, 2, 1)

        # Fileira de botões.
        btns = QHBoxLayout()
        add_btn = QPushButton("Add/Set")
        add_btn.clicked.connect(self._add_set)
        btns.addWidget(add_btn)
        btns.addStretch(1)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect_clicked)
        btns.addWidget(self.connect_btn)
        grid.addLayout(btns, 3, 0, 1, 3)

        grid.setColumnStretch(1, 1)
        return grid

    def _build_tabs(self) -> QWidget:
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        label = QLabel("Managed")
        label.setStyleSheet(
            "font-weight:bold; padding:2px 6px; "
            "background:#dde3eb; border:1px solid #b0b8c8; border-bottom:none;")
        cl.addWidget(label)

        cl.addLayout(self._build_table_toolbar())

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Address", "User"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._fill_from_selection)
        self.table.itemDoubleClicked.connect(
            lambda _item: self._connect_clicked())
        cl.addWidget(self.table)
        return container

    def _build_table_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)

        rm = QToolButton()
        rm.setIcon(icons.action_icon("remove"))
        rm.setToolTip("Remover o equipamento selecionado da lista.")
        rm.clicked.connect(self._forget)
        bar.addWidget(rm)

        filt = QToolButton()
        filt.setIcon(icons.action_icon("filter"))
        filt.setToolTip("Filtrar a lista (atalho para o campo Find).")
        filt.clicked.connect(lambda: self.find.setFocus())
        bar.addWidget(filt)

        smp = QPushButton("Set Master Password")
        smp.setEnabled(False)
        smp.setToolTip(
            "Recurso do WinBox para cifrar as senhas salvas (não usado aqui).")
        bar.addWidget(smp)

        bar.addStretch(1)

        self.find = QLineEdit()
        self.find.setObjectName("findBox")
        self.find.setPlaceholderText("Find")
        self.find.textChanged.connect(self._apply_filter)
        bar.addWidget(self.find)

        self.find_col = QComboBox()
        self.find_col.addItems(["all", "Address", "User"])
        self.find_col.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.find_col)
        return bar

    @staticmethod
    def _rlabel(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return lbl

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    # ================================================================== #
    # Lista de equipamentos
    # ================================================================== #
    def _load_table(self):
        self._profiles = load_profiles()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for p in self._profiles:
            r = self.table.rowCount()
            self.table.insertRow(r)
            addr = p.host if p.port == 22 else f"{p.host}:{p.port}"
            for c, value in enumerate([addr, p.username]):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, p)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self._apply_filter()

    def _apply_filter(self):
        text = self.find.text().strip().lower()
        scope = self.find_col.currentText()
        visible = 0
        for r in range(self.table.rowCount()):
            if not text:
                match = True
            else:
                cols = []
                if scope in ("all", "Address"):
                    cols.append(0)
                if scope in ("all", "User"):
                    cols.append(1)
                match = any(
                    text in (self.table.item(r, c).text().lower()
                             if self.table.item(r, c) else "")
                    for c in cols)
            self.table.setRowHidden(r, not match)
            visible += int(match)
        self.status.setText(
            f"{visible} item" if visible == 1 else f"{visible} items")

    def _fill_from_selection(self):
        items = self.table.selectedItems()
        if not items:
            return
        p: DeviceProfile = items[0].data(Qt.UserRole)
        self.host.setText(p.host if p.port == 22 else f"{p.host}:{p.port}")
        self.username.setText(p.username)
        self.password.setText(p.password)
        self.keep_pwd.setChecked(p.save_password)

    def _forget(self):
        items = self.table.selectedItems()
        if not items:
            return
        p: DeviceProfile = items[0].data(Qt.UserRole)
        remove_profile(p.host, p.username)
        self._load_table()

    def _parse_target(self) -> tuple[str, int]:
        text = self.host.text().strip()
        if ":" in text:
            h, _, p = text.rpartition(":")
            if p.isdigit():
                return h.strip(), int(p)
        return text, 22

    def _add_set(self):
        host, port = self._parse_target()
        username = self.username.text().strip()
        if not host or not username:
            QMessageBox.information(
                self, "Adicionar", "Informe o endereço e o usuário.")
            return
        upsert_profile(DeviceProfile(
            name=host, host=host, username=username,
            password=self.password.text() if self.keep_pwd.isChecked() else "",
            port=port, save_password=self.keep_pwd.isChecked()))
        self._load_table()
        self.status.setText(f"{host} salvo na lista.")

    # ================================================================== #
    # Conexão (com modal de troca de senha quando exigida)
    # ================================================================== #
    def _connect_clicked(self):
        host, port = self._parse_target()
        username = self.username.text().strip()
        if not host or not username:
            QMessageBox.information(
                self, "Conectar", "Informe o endereço e o usuário.")
            return
        self._start_connect(host, port, username, self.password.text(), "")

    def _start_connect(self, host: str, port: int, username: str,
                       password: str, new_password: str):
        self.connect_btn.setEnabled(False)
        self.status.setText(f"Conectando em {host}:{port} via SSH...")

        session = HuaweiSession(host, username, password, port=port)
        session.new_password = new_password

        def connect():
            session.connect()
            return session

        def on_error(message: str, _s=session, _np=new_password):
            self.connect_btn.setEnabled(True)
            self.status.setText("")
            if _s.password_change_required and not _np:
                self._ask_password_change(host, port, username, password)
            else:
                QMessageBox.critical(self, "Falha na conexão", message)

        run_async(connect, self._on_connected, on_error)

    def _ask_password_change(self, host, port, username, password):
        dlg = PasswordChangeDialog(host, username, self)
        if dlg.exec() == QDialog.Accepted:
            self._start_connect(host, port, username, password,
                                dlg.new_password())

    def _on_connected(self, session: HuaweiSession):
        # Se o perfil já existe na lista e a senha foi trocada de forma
        # obrigatória, atualiza a senha salva — sem isso o perfil ficaria
        # com a senha antiga (inválida). Não cria perfil novo automaticamente:
        # para adicionar, o usuário deve clicar em Add/Set.
        existing = next(
            (p for p in self._profiles
             if p.host == session.host and p.username == session.username),
            None)
        if existing and existing.save_password:
            upsert_profile(DeviceProfile(
                name=session.hostname,
                host=session.host,
                username=session.username,
                password=session.password,
                port=session.port,
                save_password=True,
            ))
        self.connect_btn.setEnabled(True)
        self.status.setText("")
        self.connected.emit(session)
        self.hide()
