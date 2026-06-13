"""Janela principal — menu lateral à esquerda e área MDI à direita,
no mesmo arranjo do WinBox: cada item do menu abre uma janelinha
interna independente (Interfaces, BGP, OSPF, MPLS, etc.)."""

from __future__ import annotations

import shiboken6
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
from .panels.arp import ArpPanel
from .panels.bgp import BgpPanel
from .panels.ospf import OspfPanel
from .panels.mpls import MplsPanel
from .panels.dhcp import DhcpPanel
from .panels.nat import NatPanel
from .panels.acl import AclPanel
from .panels.users import UsersPanel
from .panels.lldp import LldpPanel
from .panels.services import ServicesPanel
from .panels.snmp import SnmpPanel
from .panels.logs import LogsPanel
from .panels.tools import ToolsPanel
from .panels.system import SystemPanel
from .panels.terminal import TerminalPanel
from .panels.stp import StpPanel
from .panels.vrrp import VrrpPanel
from .panels.ipv6 import Ipv6Panel
from .panels.qos import QosPanel
from .panels.portsecurity import PortSecurityPanel
from .panels.hardware import HardwarePanel
from .panels.mactable import MacTablePanel

_MENU = [
    ("Interfaces", InterfacesPanel),
    ("Endereços IP", AddressesPanel),
    ("VLANs", VlansPanel),
    ("Tabela MAC", MacTablePanel),
    ("Rotas", RoutesPanel),
    ("ARP", ArpPanel),
    ("BGP", BgpPanel),
    ("OSPF", OspfPanel),
    ("IPv6", Ipv6Panel),
    ("MPLS / LDP", MplsPanel),
    ("STP / MSTP", StpPanel),
    ("VRRP", VrrpPanel),
    ("QoS", QosPanel),
    ("Segurança L2", PortSecurityPanel),
    ("Servidor DHCP", DhcpPanel),
    ("NAT", NatPanel),
    ("ACL / Firewall", AclPanel),
    ("Usuários", UsersPanel),
    ("Vizinhos (LLDP)", LldpPanel),
    ("Serviços", ServicesPanel),
    ("SNMP", SnmpPanel),
    ("Logs", LogsPanel),
    ("Hardware", HardwarePanel),
    ("Ferramentas", ToolsPanel),
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
        # A janela é destruída ao fechar (WA_DeleteOnClose); valida se a
        # referência registrada ainda aponta para um objeto vivo antes de
        # tentar reativá-la — senão cria uma nova.
        existing = self._windows.get(label)
        if (existing is not None and shiboken6.isValid(existing)
                and existing in self.mdi.subWindowList()):
            existing.show()
            self.mdi.setActiveSubWindow(existing)
            return
        self._windows.pop(label, None)

        panel_cls = dict(_MENU)[label]
        panel = panel_cls(self.session)
        sub = QMdiSubWindow()
        sub.setWidget(panel)
        sub.setWindowTitle(label)
        sub.setAttribute(Qt.WA_DeleteOnClose)
        # Garante a barra de título completa: fechar, minimizar e maximizar.
        sub.setWindowFlags(
            Qt.SubWindow
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowTitleHint)
        sub.destroyed.connect(
            lambda _=None, l=label: self._windows.pop(l, None))
        sub.resize(720, 420)
        self.mdi.addSubWindow(sub)
        sub.show()
        self._windows[label] = sub

    def keyPressEvent(self, event):
        """ESC fecha a janela interna ativa (como o Alt+F4 / Ctrl+F4)."""
        if event.key() == Qt.Key.Key_Escape:
            active = self.mdi.activeSubWindow()
            if active:
                active.close()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.session.close()
        super().closeEvent(event)
