"""Janela de conexão — réplica do espírito da tela inicial do WinBox:

campos *Connect To / Login / Password* em cima e a lista de
equipamentos salvos (*Managed*) embaixo, com duplo clique para conectar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QSpinBox, QCheckBox, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView,
)

from ..core.profiles import (
    DeviceProfile, load_profiles, upsert_profile, remove_profile,
)
from ..core.session import HuaweiSession
from .worker import run_async


class LoginWindow(QWidget):
    connected = Signal(object)  # emite a HuaweiSession pronta

    def __init__(self):
        super().__init__()
        self.setObjectName("loginRoot")
        self.setWindowTitle("HuaweiBox — Conectar")
        self.resize(560, 420)

        root = QVBoxLayout(self)
        title = QLabel("HuaweiBox")
        title.setObjectName("titleLabel")
        root.addWidget(title)
        root.addWidget(QLabel(
            "Gerencie roteadores e switches Huawei por cliques — "
            "o aplicativo traduz tudo para CLI e envia via SSH."))

        grid = QGridLayout()
        grid.addWidget(QLabel("Conectar em:"), 0, 0)
        self.host = QLineEdit()
        self.host.setPlaceholderText("endereço IP ou hostname")
        grid.addWidget(self.host, 0, 1)
        grid.addWidget(QLabel("Porta:"), 0, 2)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        grid.addWidget(self.port, 0, 3)

        grid.addWidget(QLabel("Usuário:"), 1, 0)
        self.username = QLineEdit()
        grid.addWidget(self.username, 1, 1)
        grid.addWidget(QLabel("Senha:"), 1, 2)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.password, 1, 3)

        root.addLayout(grid)

        opts = QHBoxLayout()
        self.save_check = QCheckBox("Salvar na lista")
        self.save_check.setChecked(True)
        self.save_pwd_check = QCheckBox("Lembrar senha")
        opts.addWidget(self.save_check)
        opts.addWidget(self.save_pwd_check)
        opts.addStretch(1)
        self.connect_btn = QPushButton("Conectar")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect_clicked)
        opts.addWidget(self.connect_btn)
        root.addLayout(opts)

        root.addWidget(QLabel("Equipamentos salvos (duplo clique conecta):"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Nome", "Endereço", "Usuário", "Porta"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._fill_from_selection)
        self.table.itemDoubleClicked.connect(
            lambda _item: self._connect_clicked())
        root.addWidget(self.table)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        forget_btn = QPushButton("Remover da lista")
        forget_btn.setObjectName("dangerBtn")
        forget_btn.clicked.connect(self._forget)
        bottom.addWidget(forget_btn)
        root.addLayout(bottom)

        self.status = QLabel("")
        root.addWidget(self.status)

        self._load_table()

    # ------------------------------------------------------------------ #
    def _load_table(self):
        self._profiles = load_profiles()
        self.table.setRowCount(0)
        for p in self._profiles:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, value in enumerate([p.name, p.host, p.username, p.port]):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, p)
                self.table.setItem(r, c, item)

    def _fill_from_selection(self):
        items = self.table.selectedItems()
        if not items:
            return
        p: DeviceProfile = items[0].data(Qt.UserRole)
        self.host.setText(p.host)
        self.username.setText(p.username)
        self.password.setText(p.password)
        self.port.setValue(p.port)
        self.save_pwd_check.setChecked(p.save_password)

    def _forget(self):
        items = self.table.selectedItems()
        if not items:
            return
        p: DeviceProfile = items[0].data(Qt.UserRole)
        remove_profile(p.host, p.username)
        self._load_table()

    # ------------------------------------------------------------------ #
    def _connect_clicked(self):
        host = self.host.text().strip()
        username = self.username.text().strip()
        if not host or not username:
            QMessageBox.information(
                self, "Conectar", "Informe o endereço e o usuário.")
            return
        self.connect_btn.setEnabled(False)
        self.status.setText(f"Conectando em {host} via SSH...")

        session = HuaweiSession(host, username, self.password.text(),
                                port=self.port.value())

        def connect():
            session.connect()
            return session

        run_async(connect, self._on_connected, self._on_error)

    def _on_connected(self, session: HuaweiSession):
        if self.save_check.isChecked():
            upsert_profile(DeviceProfile(
                name=session.hostname,
                host=session.host,
                username=session.username,
                password=session.password,
                port=session.port,
                save_password=self.save_pwd_check.isChecked(),
            ))
        self.connect_btn.setEnabled(True)
        self.status.setText("")
        self.connected.emit(session)
        self.hide()

    def _on_error(self, message: str):
        self.connect_btn.setEnabled(True)
        self.status.setText("")
        QMessageBox.critical(self, "Falha na conexão", message)
