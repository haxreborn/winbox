"""Janela principal — menu lateral à esquerda e área MDI à direita,
no mesmo arranjo do WinBox: cada item do menu abre uma janelinha
interna independente (Interfaces, BGP, OSPF, MPLS, etc.)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QMdiArea,
    QMdiSubWindow, QLabel, QStatusBar,
)

from ..core.session import HuaweiSession
from .panels.interfaces import InterfacesPanel
from .panels.addresses import AddressesPanel
from .panels.vlans import VlansPanel
from .panels.routes import RoutesPanel
from .panels.bgp import BgpPanel
from .panels.ospf import OspfPanel
from .panels.mpls import MplsPanel
from .panels.system import SystemPanel
from .panels.terminal import TerminalPanel

_MENU = [
    ("Interfaces", InterfacesPanel),
    ("Endereços IP", AddressesPanel),
    ("VLANs", VlansPanel),
    ("Rotas", RoutesPanel),
    ("BGP", BgpPanel),
    ("OSPF", OspfPanel),
    ("MPLS / LDP", MplsPanel),
    ("Sistema", SystemPanel),
    ("Novo Terminal", TerminalPanel),
]


class MainWindow(QMainWindow):
    def __init__(self, session: HuaweiSession):
        super().__init__()
        self.session = session
        self.setWindowTitle(
            f"HuaweiBox — {session.hostname} ({session.host}) "
            f"— sessão SSH de {session.username}")
        self.resize(1100, 700)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.menu = QListWidget()
        self.menu.setObjectName("sideMenu")
        self.menu.setFixedWidth(160)
        for label, _cls in _MENU:
            self.menu.addItem(label)
        self.menu.itemClicked.connect(self._open_panel)
        layout.addWidget(self.menu)

        self.mdi = QMdiArea()
        layout.addWidget(self.mdi, 1)

        self.setCentralWidget(central)

        status = QStatusBar()
        badge = QLabel("conectado")
        badge.setObjectName("connBadge")
        status.addPermanentWidget(badge)
        status.showMessage(
            "Clique num item do menu para abrir a janela correspondente. "
            "Toda configuração mostra o CLI gerado antes de ser enviada.")
        self.setStatusBar(status)

        self._windows: dict[str, QMdiSubWindow] = {}
        # Abre Interfaces de cara, como primeiro contato.
        self.menu.setCurrentRow(0)
        self._open_panel(self.menu.item(0))

    def _open_panel(self, item):
        label = item.text()
        existing = self._windows.get(label)
        if existing and not existing.isHidden():
            self.mdi.setActiveSubWindow(existing)
            return

        panel_cls = dict(_MENU)[label]
        panel = panel_cls(self.session)
        sub = QMdiSubWindow()
        sub.setWidget(panel)
        sub.setWindowTitle(label)
        sub.setAttribute(Qt.WA_DeleteOnClose)
        sub.resize(720, 420)
        self.mdi.addSubWindow(sub)
        sub.show()
        self._windows[label] = sub

    def closeEvent(self, event):
        self.session.close()
        super().closeEvent(event)
