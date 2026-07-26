"""Painel Ferramentas — ping, ping contínuo e traceroute a partir do
equipamento (equivalente ao *Tools* do WinBox).

O ping avulso responde "perdeu 20%" e não diz mais nada. O **ping
contínuo** dispara um pacote por vez, no intervalo escolhido, e desenha a
latência ao longo do tempo com as perdas marcadas em vermelho na base —
que é como se enxerga um enlace oscilando, coisa que a média min/méd/máx
esconde. O traceroute vira tabela, um salto por linha.

Tudo roda **no equipamento**, não no computador do operador, e entra na
fila com prioridade de usuário.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLineEdit, QLabel, QSpinBox, QComboBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox,
)

from ...core import parsers
from ...core.scheduler import INTERACTIVE
from ..widgets.traffic_graph import LatencyChart
from ..worker import run_async

# Traceroute com 30 saltos pode passar de um minuto.
_LONG_TIMEOUT = 90.0
_PING_INTERVALS = [1, 2, 5]


class ToolsPanel(QWidget):
    title = "Ferramentas"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._continuous = False
        self._sent = 0
        self._received = 0
        self._rtts: list[float] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_toolbar())

        self._tabs = QTabWidget()

        # ── Ping ── #
        ping_page = QWidget()
        ping_layout = QVBoxLayout(ping_page)
        ping_layout.setContentsMargins(0, 4, 0, 0)
        ping_layout.setSpacing(4)
        self.chart = LatencyChart()
        ping_layout.addWidget(self.chart, 1)
        stats_box = QGroupBox("Estatísticas da sessão")
        stats_layout = QHBoxLayout(stats_box)
        stats_layout.setContentsMargins(8, 4, 8, 6)
        self.stats = QLabel("—")
        self.stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stats_layout.addWidget(self.stats)
        stats_layout.addStretch(1)
        reset_btn = QPushButton("Zerar")
        reset_btn.clicked.connect(self._reset_stats)
        stats_layout.addWidget(reset_btn)
        ping_layout.addWidget(stats_box)
        self._tabs.addTab(ping_page, "Ping")

        # ── Traceroute ── #
        self.hops = QTableWidget(0, 3)
        self.hops.setHorizontalHeaderLabels(["Salto", "Endereço", "Tempos"])
        self.hops.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hops.setAlternatingRowColors(True)
        self.hops.verticalHeader().setVisible(False)
        header = self.hops.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self._tabs.addTab(self.hops, "Traceroute")

        # ── Saída bruta ── #
        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        self.output.setPlainText(
            "Os testes são executados a partir do equipamento, não do seu "
            "computador.\nInforme um destino e clique em Ping, Ping contínuo "
            "ou Traceroute.")
        self._tabs.addTab(self.output, "Saída bruta")
        root.addWidget(self._tabs, 1)

        self.status = QLabel("")
        root.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._ping_tick)

    # ================================================================== #
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(QLabel("Destino:"))
        self.target = QLineEdit()
        self.target.setPlaceholderText("IP ou hostname (ex.: 8.8.8.8)")
        self.target.returnPressed.connect(self._ping)
        bar.addWidget(self.target, 1)

        bar.addWidget(QLabel("Origem:"))
        self.source = QLineEdit()
        self.source.setPlaceholderText("(opcional)")
        self.source.setMaximumWidth(130)
        self.source.setToolTip(
            "Endereço ou interface de origem (-a no VRP). Útil para testar "
            "a partir do IP que o cliente enxerga.")
        bar.addWidget(self.source)

        bar.addWidget(QLabel("VPN:"))
        self.vpn = QLineEdit()
        self.vpn.setPlaceholderText("(global)")
        self.vpn.setMaximumWidth(110)
        self.vpn.setToolTip("Instância VPN/VRF (-vpn-instance).")
        bar.addWidget(self.vpn)

        bar.addWidget(QLabel("Pacotes:"))
        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(5)
        bar.addWidget(self.count)

        self.ping_btn = QPushButton("Ping")
        self.ping_btn.clicked.connect(self._ping)
        bar.addWidget(self.ping_btn)

        self.cont_btn = QPushButton("Ping contínuo")
        self.cont_btn.setCheckable(True)
        self.cont_btn.setToolTip(
            "Dispara um pacote por vez e desenha a latência ao longo do "
            "tempo, marcando as perdas.")
        self.cont_btn.toggled.connect(self._toggle_continuous)
        bar.addWidget(self.cont_btn)

        self.interval_combo = QComboBox()
        for secs in _PING_INTERVALS:
            self.interval_combo.addItem(f"{secs}s", secs)
        self.interval_combo.currentIndexChanged.connect(self._interval_changed)
        bar.addWidget(self.interval_combo)

        self.trace_btn = QPushButton("Traceroute")
        self.trace_btn.clicked.connect(self._trace)
        bar.addWidget(self.trace_btn)
        return bar

    # ================================================================== #
    # Montagem do comando
    # ================================================================== #
    def _target(self) -> str:
        return self.target.text().strip()

    def _ping_command(self, count: int) -> str:
        parts = ["ping"]
        vpn = self.vpn.text().strip()
        if vpn:
            parts += ["-vpn-instance", vpn]
        source = self.source.text().strip()
        if source:
            parts += ["-a", source]
        parts += ["-c", str(count), self._target()]
        return " ".join(parts)

    def _trace_command(self) -> str:
        parts = ["tracert"]
        vpn = self.vpn.text().strip()
        if vpn:
            parts += ["-vpn-instance", vpn]
        source = self.source.text().strip()
        if source:
            parts += ["-a", source]
        parts.append(self._target())
        return " ".join(parts)

    # ================================================================== #
    # Ping avulso
    # ================================================================== #
    def _ping(self) -> None:
        if not self._require_target():
            return
        command = self._ping_command(self.count.value())
        self._run(command, self._on_ping_result)

    def _on_ping_result(self, text: str, command: str) -> None:
        result = parsers.parse_ping(text)
        now = time.time()
        for rtt in result["rtts"]:
            self.chart.add(now, rtt)
            self._rtts.append(rtt)
        for _ in range(result["lost"]):
            self.chart.add(now, None)
        self._sent += result["sent"] or 0
        self._received += result["received"] or 0
        self.chart.set_title(f"Latência até {self._target()}")
        self._update_stats()
        self._tabs.setCurrentIndex(0)

    # ================================================================== #
    # Ping contínuo
    # ================================================================== #
    def _toggle_continuous(self, running: bool) -> None:
        if running and not self._require_target():
            self.cont_btn.setChecked(False)
            return
        self._continuous = running
        self.cont_btn.setText("Parar" if running else "Ping contínuo")
        self.ping_btn.setEnabled(not running)
        self.trace_btn.setEnabled(not running)
        if running:
            self.chart.set_title(f"Latência até {self._target()}")
            self._timer.start(
                (self.interval_combo.currentData() or 1) * 1000)
            self._ping_tick()
        else:
            self._timer.stop()

    def _interval_changed(self) -> None:
        if self._continuous:
            self._timer.start(
                (self.interval_combo.currentData() or 1) * 1000)

    def _ping_tick(self) -> None:
        if self._busy or not self._continuous:
            return
        self._busy = True
        command = self._ping_command(1)
        session = self.session

        def fetch() -> str:
            return session.run(command, timeout=20.0, priority=INTERACTIVE,
                               tag="tools")

        def done(text: str) -> None:
            self._busy = False
            result = parsers.parse_ping(text)
            now = time.time()
            if result["rtts"]:
                for rtt in result["rtts"]:
                    self.chart.add(now, rtt)
                    self._rtts.append(rtt)
                self._received += len(result["rtts"])
            else:
                self.chart.add(now, None)
            self._sent += max(1, result["sent"] or 1)
            self._update_stats()

        def fail(message: str) -> None:
            self._busy = False
            self._sent += 1
            self.chart.add(time.time(), None)
            self._update_stats()
            self.status.setText(f"⚠ {message.splitlines()[0][:110]}")

        run_async(fetch, done, fail)

    def _update_stats(self) -> None:
        lost = max(0, self._sent - self._received)
        loss = (lost / self._sent * 100.0) if self._sent else 0.0
        parts = [f"enviados {self._sent}", f"recebidos {self._received}",
                 f"perdidos {lost} ({loss:.1f}%)"]
        if self._rtts:
            recent = self._rtts[-100:]
            parts.append(f"último {self._rtts[-1]:.0f} ms")
            parts.append(f"mín {min(recent):.0f} · méd "
                         f"{sum(recent) / len(recent):.0f} · "
                         f"máx {max(recent):.0f} ms")
            if len(recent) > 2:
                # Variação (jitter) como média das diferenças consecutivas:
                # é o número que denuncia um enlace instável mesmo com a
                # média baixa.
                jitter = sum(abs(recent[i] - recent[i - 1])
                             for i in range(1, len(recent))) / (len(recent) - 1)
                parts.append(f"variação {jitter:.0f} ms")
        self.stats.setText("   ·   ".join(parts))

    def _reset_stats(self) -> None:
        self._sent = self._received = 0
        self._rtts.clear()
        self.chart.clear()
        self._update_stats()

    # ================================================================== #
    # Traceroute
    # ================================================================== #
    def _trace(self) -> None:
        if not self._require_target():
            return
        self._run(self._trace_command(), self._on_trace_result)

    def _on_trace_result(self, text: str, command: str) -> None:
        hops = parsers.parse_traceroute(text)
        self.hops.setRowCount(len(hops))
        for row, hop in enumerate(hops):
            times = "  ".join(f"{t:.0f} ms" for t in hop["times"]) or "sem resposta"
            for column, value in enumerate((hop["hop"], hop["host"] or "*",
                                            times)):
                item = QTableWidgetItem(str(value))
                if not hop["times"]:
                    item.setForeground(Qt.darkGray)
                self.hops.setItem(row, column, item)
        self._tabs.setCurrentIndex(1)
        self.status.setText(f"{len(hops)} salto(s) até {self._target()}")

    # ================================================================== #
    def _require_target(self) -> bool:
        if self._target():
            return True
        self.status.setText("Informe um destino primeiro.")
        return False

    def _run(self, command: str, on_parsed) -> None:
        self.ping_btn.setEnabled(False)
        self.trace_btn.setEnabled(False)
        self.status.setText(f"Executando: {command}")
        self.output.appendPlainText(f"\n> {command}\n")
        session = self.session

        def fetch() -> str:
            return session.run(command, timeout=_LONG_TIMEOUT,
                               priority=INTERACTIVE, tag="tools")

        def done(text: str) -> None:
            self._unlock()
            self.output.appendPlainText(
                text.strip() or "(sem resposta do equipamento)")
            self.status.setText("")
            try:
                on_parsed(text, command)
            except Exception as exc:          # noqa: BLE001 — saída exótica
                self.status.setText(
                    f"Saída recebida, mas não reconhecida ({exc}). "
                    f"Veja a aba 'Saída bruta'.")

        def fail(message: str) -> None:
            self._unlock()
            self.output.appendPlainText(f"[erro] {message}")
            self.status.setText(f"⚠ {message.splitlines()[0][:110]}")

        run_async(fetch, done, fail)

    def _unlock(self) -> None:
        self.ping_btn.setEnabled(not self._continuous)
        self.trace_btn.setEnabled(not self._continuous)

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if self._continuous and not self._timer.isActive():
            self._timer.start(
                (self.interval_combo.currentData() or 1) * 1000)
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self._continuous = False
        self._timer.stop()
        self.session.scheduler.cancel("tools")
        super().closeEvent(event)
