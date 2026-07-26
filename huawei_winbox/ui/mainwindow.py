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

from ..core.session import HuaweiSession, INTERACTIVE
from ..core import telemetry, workspace
from ..core import history as history_mod
from . import icons
from .forms import SimpleForm
from .palette import CommandPalette
from .worker import run_async
from .panels.dashboard import DashboardPanel
from .panels.interfaces import InterfacesPanel
from .panels.traffic import TrafficPanel
from .panels.config import ConfigPanel
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
from .panels.firewall import FirewallPanel
from .panels.users import UsersPanel
from .panels.lldp import LldpPanel
from .panels.services import ServicesPanel
from .panels.snmp import SnmpPanel
from .panels.logs import LogsPanel
from .panels.alarms import AlarmsPanel
from .panels.porthealth import PortHealthPanel
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
    ("Painel", DashboardPanel),
    ("Interfaces", InterfacesPanel),
    ("Tráfego (gráfico)", TrafficPanel),
    ("Configuração", ConfigPanel),
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
    ("Firewall / NAT", FirewallPanel),
    ("Usuários", UsersPanel),
    ("Vizinhos (LLDP)", LldpPanel),
    ("Serviços", ServicesPanel),
    ("SNMP", SnmpPanel),
    ("Saúde das portas", PortHealthPanel),
    ("Alarmes", AlarmsPanel),
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


def _load_color(pct: float | None) -> str:
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
        self.resize(1240, 780)
        self._windows: dict[str, QMdiSubWindow] = {}
        # Coletor único da aplicação: a barra de ferramentas, o Painel e o
        # gráfico de tráfego leem daqui em vez de cada um consultar o
        # equipamento por conta própria.
        self.history = history_mod.for_session(session)
        self.telemetry = telemetry.for_session(session, self.history)
        # Preferências deste equipamento (janelas abertas, fonte e cadência
        # da telemetria, comunidade SNMP). Aplicadas antes de subir o
        # coletor para ele já começar do jeito certo.
        self._workspace = workspace.load(session.host)
        self._apply_workspace_telemetry()
        self.telemetry.start()

        self._build_menubar()
        self._build_toolbar()

        # ── Corpo: faixa do produto | menu lateral | MDI ─────────────── #
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(_ProductStrip("HuaweiBox"))

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
        self.queue_label = QLabel("")
        self.queue_label.setObjectName("loadText")
        self.queue_label.setToolTip(
            "Estado da fila de comandos SSH. Cliques do usuário passam à "
            "frente das atualizações automáticas, e consultas repetidas "
            "entre janelas são unificadas.")
        status.addPermanentWidget(self.queue_label)
        badge = QLabel("conectado")
        badge.setObjectName("connBadge")
        status.addPermanentWidget(badge)
        status.showMessage(
            "Clique num item do menu para abrir a janela correspondente. "
            "Toda configuração mostra o CLI gerado antes de ser enviada.")
        self.setStatusBar(status)

        # A barra só LÊ o retrato do coletor — nada de consulta própria.
        self._load_timer = QTimer(self)
        self._load_timer.timeout.connect(self._poll_load)
        self._set_load_interval(5)

        # Reabre o que estava aberto na última sessão com este equipamento;
        # na primeira vez, abre o Painel — a visão geral de uma vez só.
        if not self._restore_windows():
            self.menu.setCurrentRow(0)
            self._open_panel(self.menu.item(0))
        QTimer.singleShot(400, self._poll_load)

    # ================================================================== #
    # Preferências por equipamento
    # ================================================================== #
    def _apply_workspace_telemetry(self) -> None:
        saved = self._workspace
        community = saved.get("snmp_community")
        if community:
            self.telemetry.configure_snmp(community,
                                          int(saved.get("snmp_port") or 161))
        source = saved.get("telemetry_source")
        if source in telemetry.SOURCE_LABELS:
            self.telemetry.set_source(source)
        interval = saved.get("telemetry_interval")
        if isinstance(interval, (int, float)) and interval > 0:
            self.telemetry.set_interval(interval)

    def _restore_windows(self) -> bool:
        """Reabre as janelas da última sessão. True se abriu alguma."""
        entries = self._workspace.get("windows")
        if not isinstance(entries, list) or not entries:
            return False
        known = dict(_MENU)
        opened = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            if label not in known:
                continue        # módulo renomeado ou removido numa atualização
            items = self.menu.findItems(label, Qt.MatchExactly)
            if not items:
                continue
            try:
                self._open_panel(items[0])
            except Exception:            # noqa: BLE001 — layout nunca bloqueia
                continue
            window = self._windows.get(label)
            geometry = entry.get("geometry")
            if window is not None and isinstance(geometry, list) \
                    and len(geometry) == 4:
                try:
                    window.setGeometry(*(int(v) for v in geometry))
                except (TypeError, ValueError):
                    pass
            opened += 1
        return opened > 0

    def _collect_workspace(self) -> dict:
        windows = []
        for label, window in self._windows.items():
            if window is None or not shiboken6.isValid(window):
                continue
            rect = window.geometry()
            windows.append({
                "label": label,
                "geometry": [rect.x(), rect.y(), rect.width(), rect.height()],
            })
        snapshot = self.telemetry.snapshot()
        values = {
            "windows": windows,
            "telemetry_source": snapshot["requested_source"],
            "telemetry_interval": snapshot["interval"],
        }
        if self.telemetry.community and self.telemetry.community != "public":
            values["snmp_community"] = self.telemetry.community
            values["snmp_port"] = self.telemetry.snmp_port
        # Cada painel pode contribuir com o seu próprio estado.
        for label, window in self._windows.items():
            if window is None or not shiboken6.isValid(window):
                continue
            panel = window.widget()
            collect = getattr(panel, "collect_state", None)
            if callable(collect):
                try:
                    state = collect()
                except Exception:        # noqa: BLE001 — nunca impede o fechar
                    continue
                if isinstance(state, dict):
                    # Chave própria do painel quando existir: sobrevive a
                    # uma renomeação do item de menu.
                    key = getattr(panel, "state_key", None) or label
                    values.setdefault("panels", {})[key] = state
        return values

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

        # Coleta de telemetria: vale para o gráfico, o Painel e a barra.
        src_menu = m_cfg.addMenu("Fonte da telemetria")
        src_group = QActionGroup(self)
        for key in (telemetry.SOURCE_AUTO, telemetry.SOURCE_SNMP,
                    telemetry.SOURCE_CLI):
            action = QAction(telemetry.SOURCE_LABELS[key], self, checkable=True)
            action.setChecked(key == telemetry.SOURCE_AUTO)
            action.triggered.connect(
                lambda _=False, k=key: self.telemetry.set_source(k))
            src_group.addAction(action)
            src_menu.addAction(action)

        rate_menu = m_cfg.addMenu("Amostrar tráfego a cada")
        rate_group = QActionGroup(self)
        for secs in (1, 2, 3, 5, 10, 30):
            action = QAction(f"{secs} s", self, checkable=True)
            action.setChecked(secs == 2)
            action.triggered.connect(
                lambda _=False, s=secs: self.telemetry.set_interval(s))
            rate_group.addAction(action)
            rate_menu.addAction(action)

        act_snmp = QAction("Comunidade SNMP para leitura…", self)
        act_snmp.setToolTip(
            "Com o agente SNMP habilitado, a coleta usa GET-BULK: todas as "
            "interfaces numa amostra só, sem gastar a sessão SSH.")
        act_snmp.triggered.connect(self._ask_snmp_community)
        m_cfg.addAction(act_snmp)

        m_win = bar.addMenu("&Janelas")
        for label, slot in [
            ("Lado a lado", lambda: self.mdi.tileSubWindows()),
            ("Em cascata", lambda: self.mdi.cascadeSubWindows()),
            ("Fechar todas", lambda: self.mdi.closeAllSubWindows()),
        ]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            m_win.addAction(a)

        act_find = QAction("Ir para módulo…", self)
        act_find.setShortcut("Ctrl+K")
        act_find.setToolTip("Abre qualquer módulo ou ação digitando o nome.")
        act_find.triggered.connect(self._open_palette)
        m_win.addSeparator()
        m_win.addAction(act_find)

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

        # Safe Mode — nos equipamentos NE/ATN com suporte a commit trial,
        # cada configuração usa "commit trial 600" (10 min para rollback
        # automático se não confirmado). Em dispositivos sem suporte,
        # o botão fica desabilitado.
        self.safe_btn = QPushButton("Safe Mode")
        self.safe_btn.setObjectName("safeMode")
        self.safe_btn.setCheckable(True)
        self.safe_btn.setEnabled(self.session.supports_safe_mode)
        if self.session.supports_safe_mode:
            self.safe_btn.setToolTip(
                "Ativo: cada alteração usa 'commit trial 600'.\n"
                "A config candidata é revertida automaticamente após 10 min\n"
                "se não for confirmada com 'Salvar configuração'.")
        else:
            tip = ("Safe Mode não disponível neste equipamento.\n"
                   "Requer VRP dois estágios (NE/ATN) com suporte a "
                   "'commit trial'.")
            self.safe_btn.setToolTip(tip)
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
            # O coletor amostra na cadência do gráfico; este intervalo só
            # controla de quanto em quanto tempo a barra relê o retrato.
            self._load_timer.start(max(1, secs) * 1000)
            self._poll_load()
        else:
            self._load_timer.stop()
            self.mem_label.setText("Memória: —")
            self.cpu_label.setText("CPU: —")
            self._paint_square(self.mem_square, None)
            self._paint_square(self.cpu_square, None)

    def _poll_load(self) -> None:
        """Lê o último retrato do coletor — sem tocar no equipamento."""
        if not self.session.connected:
            return
        snapshot = self.telemetry.snapshot()
        health = snapshot["health"]
        cpu, mem = health.get("cpu"), health.get("memory")
        self.cpu_label.setText(
            f"CPU: {cpu:.0f}%" if cpu is not None else "CPU: —")
        self.mem_label.setText(
            f"Memória: {mem:.0f}%" if mem is not None else "Memória: —")
        self._paint_square(self.cpu_square, cpu)
        self._paint_square(self.mem_square, mem)

        stats = self.session.scheduler.stats()
        pieces = []
        if stats["depth"]:
            pieces.append(f"fila {stats['depth']}")
        saved = stats["cache_hits"] + stats["coalesced"]
        if saved:
            pieces.append(f"{saved} consultas evitadas")
        if stats["dropped"]:
            pieces.append(f"{stats['dropped']} descartadas")
        self.queue_label.setText(" · ".join(pieces))

    @staticmethod
    def _paint_square(square: QLabel, pct: float | None) -> None:
        square.setStyleSheet(
            f"background:{_load_color(pct)}; border:1px solid #6a6a6a; "
            f"border-radius:2px;")

    # ================================================================== #
    # Ações
    # ================================================================== #
    def _palette_entries(self) -> list[tuple[str, str, object]]:
        """Módulos e ações globais oferecidos pela busca (Ctrl+K)."""
        entries: list[tuple[str, str, object]] = [
            (label, "módulo",
             (lambda l=label: self.open_named_panel(l)))
            for label, _cls in _MENU
        ]
        entries += [
            ("Salvar configuração na flash", "ação", self._save_config),
            ("Fechar todas as janelas", "ação",
             self.mdi.closeAllSubWindows),
            ("Organizar janelas lado a lado", "ação", self.mdi.tileSubWindows),
            ("Organizar janelas em cascata", "ação",
             self.mdi.cascadeSubWindows),
            ("Comunidade SNMP para leitura", "ação", self._ask_snmp_community),
            ("Desconectar", "ação", self.close),
        ]
        if self.session.supports_safe_mode:
            entries.append(("Alternar Safe Mode", "ação",
                            self.safe_btn.toggle))
        return entries

    def _open_palette(self) -> None:
        dialog = CommandPalette(self._palette_entries(), self)
        dialog.exec()

    def _ask_snmp_community(self) -> None:
        values = SimpleForm.ask(
            "Coleta via SNMP (somente leitura)",
            [("community", "Comunidade de leitura", "text", "public"),
             ("port", "Porta UDP", "int", 161)],
            self)
        if not values:
            return
        self.telemetry.configure_snmp(values["community"], values["port"])
        self.telemetry.poke()
        self.statusBar().showMessage(
            "Comunidade SNMP aplicada — a coleta passa a usar SNMP assim que "
            "o agente responder.", 6000)

    def _safe_toggled(self, on: bool) -> None:
        self.session.safe_mode = on
        if on:
            self.statusBar().showMessage(
                "Safe Mode ativado — cada alteração usa 'commit trial 600'. "
                "A configuração candidata será revertida em 10 min se não for "
                "confirmada via Sessão → Salvar configuração.", 8000)
        else:
            self.statusBar().showMessage("Safe Mode desativado.", 4000)

    def _save_config(self) -> None:
        if QMessageBox.question(
                self, "Salvar configuração",
                "Gravar a configuração atual na flash do equipamento "
                "(comando 'save')?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.statusBar().showMessage("Salvando configuração...", 0)

        def do_save():
            return self.session.run("save", timeout=60.0,
                                    priority=INTERACTIVE)

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
    def open_named_panel(self, label: str, payload=None) -> None:
        """Abre um módulo pelo nome e, se ele aceitar, entrega um alvo.

        É o que liga os painéis entre si — "Ver no gráfico" na tabela de
        Interfaces abre o Tráfego já com aquelas portas marcadas, em vez de
        obrigar a procurá-las de novo na lista.
        """
        items = self.menu.findItems(label, Qt.MatchExactly)
        if not items:
            return
        self.menu.setCurrentItem(items[0])
        self._open_panel(items[0])
        window = self._windows.get(label)
        if window is None or not shiboken6.isValid(window):
            return
        # Traz para a frente explicitamente: a janela pode já estar aberta
        # atrás de outra, e quem pediu espera vê-la.
        window.show()
        window.raise_()
        self.mdi.setActiveSubWindow(window)
        panel = window.widget()
        if payload is not None and hasattr(panel, "focus_payload"):
            panel.focus_payload(payload)

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
        # Painéis que sabem pedir a abertura de outro módulo (opcional).
        request = getattr(panel, "open_panel_requested", None)
        if request is not None:
            request.connect(self.open_named_panel)
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
        try:
            workspace.save(self.session.host, self._collect_workspace())
        except Exception:               # noqa: BLE001 — nunca trava o fechar
            pass
        telemetry.release(self.session)
        self.session.close()
        super().closeEvent(event)
