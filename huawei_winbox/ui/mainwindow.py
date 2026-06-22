"""Janela principal — réplica do arranjo do WinBox.

De cima para baixo: barra de menus (Sessão/Configurações/Janelas/Ajuda),
barra de ferramentas (navegação, Safe Mode, identificação da sessão e
indicadores de Memória/CPU ao vivo), e o corpo com a faixa vertical do
produto + menu lateral com ícones + área MDI de janelas internas.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QActionGroup, QPainter, QColor, QLinearGradient
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QMdiArea,
    QMdiSubWindow, QLabel, QStatusBar, QToolBar, QToolButton, QPushButton,
    QMessageBox, QApplication, QStyle, QSizePolicy,
)

from ..core.session import HuaweiSession
from ..core import parsers
from . import icons
from .worker import run_async
from .panels.interfaces import InterfacesPanel
from .panels.traffic import TrafficPanel
from .panels.addresses import AddressesPanel
from .panels.vlans import VlansPanel
from .panels.routes import RoutesPanel
from .panels.arp import ArpPanel
from .panels.bgp import BgpPanel
from .panels.ospf import OspfPanel
from .panels.mpls import MplsPanel
from .panels.dhcp import DhcpPanel
from .panels.pppoe import PppoePanel
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
    ("Tráfego (gráfico)", TrafficPanel),
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
    ("PPPoE / BAS", PppoePanel),
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


class _ProductStrip(QWidget):
    """Faixa vertical azul com o nome do produto, como no WinBox."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text
        self.setFixedWidth(22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, QColor("#2f5fa0"))
        grad.setColorAt(1.0, QColor("#4078c4"))
        p.fillRect(self.rect(), grad)
        p.setPen(QColor("#eaf2fc"))
        f = p.font()
        f.setBold(True)
        f.setPixelSize(12)          # tema usa px; pointSizeF() seria -1
        p.setFont(f)
        p.translate(self.width() - 5, self.height() - 10)
        p.rotate(-90)
        p.drawText(0, 0, self._text)
        p.end()


def _load_color(pct: int | None) -> str:
    if pct is None:
        return "#9a9a9a"
    if pct < 60:
        return "#2e8b57"
    if pct < 85:
        return "#d08a1f"
    return "#c0392b"


class MainWindow(QMainWindow):
    def __init__(self, session: HuaweiSession):
        super().__init__()
        self.session = session
        self.setWindowTitle(
            f"HuaweiBox — {session.hostname} ({session.host}) "
            f"— sessão SSH de {session.username}")
        self.resize(1140, 720)
        self._windows: dict[str, QMdiSubWindow] = {}
        self._load_busy = False

        self._build_menubar()
        self._build_toolbar()

        # ── Corpo: faixa do produto | menu lateral | MDI ─────────────── #
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(_ProductStrip("HuaweiBox  ·  estilo WinBox"))

        self.menu = QListWidget()
        self.menu.setObjectName("sideMenu")
        self.menu.setFixedWidth(168)
        self.menu.setIconSize(QSize(18, 18))
        for label, _cls in _MENU:
            self.menu.addItem(label)
            self.menu.item(self.menu.count() - 1).setIcon(
                icons.menu_icon(label))
        self.menu.itemClicked.connect(self._open_panel)
        layout.addWidget(self.menu)

        self.mdi = QMdiArea()
        layout.addWidget(self.mdi, 1)
        self.setCentralWidget(central)

        # ── Status bar ────────────────────────────────────────────────── #
        status = QStatusBar()
        badge = QLabel("conectado")
        badge.setObjectName("connBadge")
        status.addPermanentWidget(badge)
        status.showMessage(
            "Clique num item do menu para abrir a janela correspondente. "
            "Toda configuração mostra o CLI gerado antes de ser enviada.")
        self.setStatusBar(status)

        # Indicadores de carga (Memória/CPU) atualizados periodicamente.
        self._load_timer = QTimer(self)
        self._load_timer.timeout.connect(self._poll_load)
        self._set_load_interval(5)

        # Abre Interfaces de cara, como primeiro contato.
        self.menu.setCurrentRow(0)
        self._open_panel(self.menu.item(0))
        QTimer.singleShot(400, self._poll_load)

    # ================================================================== #
    # Construção da casca
    # ================================================================== #
    def _build_menubar(self) -> None:
        bar = self.menuBar()

        m_session = bar.addMenu("&Sessão")
        act_save = QAction("Salvar configuração", self)
        act_save.triggered.connect(self._save_config)
        m_session.addAction(act_save)
        m_session.addSeparator()
        act_disc = QAction("Desconectar", self)
        act_disc.triggered.connect(self.close)
        m_session.addAction(act_disc)
        act_quit = QAction("Sair", self)
        act_quit.triggered.connect(QApplication.quit)
        m_session.addAction(act_quit)

        m_cfg = bar.addMenu("&Configurações")
        sub = m_cfg.addMenu("Atualizar Memória/CPU a cada")
        grp = QActionGroup(self)
        for secs, label in [(0, "Desligado"), (3, "3 s"), (5, "5 s"),
                            (10, "10 s"), (30, "30 s")]:
            a = QAction(label, self, checkable=True)
            a.setChecked(secs == 5)
            a.triggered.connect(lambda _=False, s=secs: self._set_load_interval(s))
            grp.addAction(a)
            sub.addAction(a)

        m_win = bar.addMenu("&Janelas")
        for label, slot in [
            ("Lado a lado", lambda: self.mdi.tileSubWindows()),
            ("Em cascata", lambda: self.mdi.cascadeSubWindows()),
            ("Fechar todas", lambda: self.mdi.closeAllSubWindows()),
        ]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            m_win.addAction(a)

        m_help = bar.addMenu("&Ajuda")
        a_about = QAction("Sobre o HuaweiBox", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Principal")
        tb.setObjectName("winToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, tb)

        # Navegação (decorativa, como o estado inicial do WinBox)
        for sp, tip in [(QStyle.SP_ArrowBack, "Voltar (reservado)"),
                        (QStyle.SP_ArrowForward, "Avançar (reservado)")]:
            btn = QToolButton()
            btn.setIcon(self.style().standardIcon(sp))
            btn.setToolTip(tip)
            btn.setEnabled(False)
            tb.addWidget(btn)
        tb.addSeparator()

        # Safe Mode — indicador visual (Huawei usa commit/rollback nos NE).
        self.safe_btn = QPushButton("Safe Mode")
        self.safe_btn.setObjectName("safeMode")
        self.safe_btn.setCheckable(True)
        self.safe_btn.setToolTip(
            "Indicador visual no estilo WinBox.\nNos equipamentos NE/ATN "
            "(dois estágios) o HuaweiBox já envia 'commit' a cada alteração; "
            "use 'Salvar configuração' para gravar na flash.")
        self.safe_btn.toggled.connect(self._safe_toggled)
        tb.addWidget(self.safe_btn)
        tb.addSeparator()

        tb.addWidget(QLabel(" Sessão: "))
        self.session_field = QLabel(f"{self.session.host}")
        self.session_field.setObjectName("sessionField")
        self.session_field.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tb.addWidget(self.session_field)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        # Indicadores Memória / CPU
        self.mem_square = self._square()
        tb.addWidget(self.mem_square)
        self.mem_label = QLabel("Memória: —")
        self.mem_label.setObjectName("loadText")
        tb.addWidget(self.mem_label)
        tb.addWidget(self._spacer(10))
        self.cpu_square = self._square()
        tb.addWidget(self.cpu_square)
        self.cpu_label = QLabel("CPU: —")
        self.cpu_label.setObjectName("loadText")
        tb.addWidget(self.cpu_label)
        tb.addWidget(self._spacer(6))

    @staticmethod
    def _square() -> QLabel:
        sq = QLabel()
        sq.setFixedSize(11, 11)
        sq.setStyleSheet(
            "background:#9a9a9a; border:1px solid #6a6a6a; border-radius:2px;")
        return sq

    @staticmethod
    def _spacer(width: int) -> QWidget:
        w = QWidget()
        w.setFixedWidth(width)
        return w

    # ================================================================== #
    # Indicadores Memória / CPU
    # ================================================================== #
    def _set_load_interval(self, secs: int) -> None:
        self._load_interval = secs
        if secs:
            self._load_timer.start(secs * 1000)
            self._poll_load()
        else:
            self._load_timer.stop()
            self.mem_label.setText("Memória: —")
            self.cpu_label.setText("CPU: —")
            self._paint_square(self.mem_square, None)
            self._paint_square(self.cpu_square, None)

    def _poll_load(self) -> None:
        if self._load_busy or not self.session.connected:
            return
        self._load_busy = True

        def fetch():
            cpu = parsers.parse_cpu_usage(
                self.session.run("display cpu-usage", timeout=8.0))
            mem = parsers.parse_memory_usage(
                self.session.run("display memory-usage", timeout=8.0))
            return cpu, mem

        run_async(fetch, self._on_load, self._on_load_err)

    def _on_load(self, data) -> None:
        self._load_busy = False
        cpu, mem = data
        self.cpu_label.setText(f"CPU: {cpu}")
        self.mem_label.setText(f"Memória: {mem}")
        self._paint_square(self.cpu_square, self._pct(cpu))
        self._paint_square(self.mem_square, self._pct(mem))

    def _on_load_err(self, _msg: str) -> None:
        self._load_busy = False

    @staticmethod
    def _pct(text: str) -> int | None:
        try:
            return int(str(text).rstrip("%"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _paint_square(square: QLabel, pct: int | None) -> None:
        square.setStyleSheet(
            f"background:{_load_color(pct)}; border:1px solid #6a6a6a; "
            f"border-radius:2px;")

    # ================================================================== #
    # Ações
    # ================================================================== #
    def _safe_toggled(self, on: bool) -> None:
        self.statusBar().showMessage(
            "Safe Mode ligado (indicador visual)." if on
            else "Safe Mode desligado.", 4000)

    def _save_config(self) -> None:
        if QMessageBox.question(
                self, "Salvar configuração",
                "Gravar a configuração atual na flash do equipamento "
                "(comando 'save')?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.statusBar().showMessage("Salvando configuração...", 0)

        def do_save():
            return self.session.run("save", timeout=60.0)

        def done(out: str):
            self.statusBar().showMessage("Configuração salva.", 5000)

        def fail(msg: str):
            self.statusBar().showMessage("", 0)
            QMessageBox.warning(self, "Salvar configuração", msg)

        run_async(do_save, done, fail)

    def _about(self) -> None:
        QMessageBox.about(
            self, "Sobre o HuaweiBox",
            "<b>HuaweiBox</b> — gerenciador gráfico estilo WinBox para "
            "equipamentos Huawei (VRP).<br><br>"
            "Clique na interface, revise o CLI gerado e envie via SSH. "
            "Inclui gráfico de tráfego (CLI/SNMP), BGP/OSPF/MPLS, VLANs, "
            "QoS, ACL, NAT, DHCP e terminal integrado.")

    # ================================================================== #
    # Painéis (janelas internas)
    # ================================================================== #
    def _open_panel(self, item):
        label = item.text()
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
        sub.setWindowIcon(icons.menu_icon(label))
        sub.setAttribute(Qt.WA_DeleteOnClose)
        sub.setWindowFlags(
            Qt.SubWindow
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowTitleHint)
        sub.destroyed.connect(
            lambda _=None, l=label: self._windows.pop(l, None))
        sub.resize(760, 440)
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
        self._load_timer.stop()
        self.session.close()
        super().closeEvent(event)
