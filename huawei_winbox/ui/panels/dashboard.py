"""Painel de visão geral — várias leituras ao vivo na mesma janela.

Reúne num só lugar o que antes exigia abrir (e ficar alternando entre)
quatro ou cinco janelas: carga do equipamento, banda total, ranking das
portas mais carregadas, estado das interfaces e dos protocolos de
roteamento, além da identificação do equipamento.

Nada aqui consulta o equipamento por conta própria para o tráfego: os
números vêm do coletor único (:mod:`~huawei_winbox.core.telemetry`), que
já amostra todas as portas. As informações lentas (versão, BGP, OSPF)
são pedidas com prioridade de fundo, com cache e com prazo de validade —
se o equipamento estiver ocupado, o pedido é descartado em vez de entrar
na frente do que o usuário está fazendo.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QStyledItemDelegate,
    QSizePolicy, QPushButton, QCheckBox, QFileDialog, QMessageBox,
)

from ...core import parsers, report, telemetry
from ...core import history as history_mod
from ...core.scheduler import BACKGROUND
from ..worker import run_async
from ..widgets.traffic_graph import MiniChart

_REFRESH_MS = 1000
_SLOW_EVERY = 30.0          # segundos entre as consultas caras
_TOP_ROWS = 12


def _load_color(pct: float | None) -> QColor:
    if pct is None:
        return QColor("#9a9a9a")
    if pct < 60:
        return QColor("#2e8b57")
    if pct < 85:
        return QColor("#d08a1f")
    return QColor("#c0392b")


class _BarDelegate(QStyledItemDelegate):
    """Desenha a utilização da porta como uma barra dentro da célula."""

    def paint(self, painter: QPainter, option, index) -> None:
        pct = index.data(Qt.UserRole)
        if pct is None:
            super().paint(painter, option, index)
            return
        super().paint(painter, option, index)
        rect = QRectF(option.rect).adjusted(3, 4, -3, -4)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e8ebee"))
        painter.drawRoundedRect(rect, 2, 2)
        filled = QRectF(rect)
        filled.setWidth(rect.width() * max(0.0, min(1.0, pct / 100.0)))
        painter.setBrush(_load_color(pct))
        painter.drawRoundedRect(filled, 2, 2)
        painter.setPen(QColor("#1c1c1c"))
        font = QFont(option.font)
        font.setPointSizeF(max(6.5, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, f"{pct:.1f}%")
        painter.restore()


class DashboardPanel(QWidget):
    title = "Painel"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history = history_mod.for_session(session)
        self.engine = telemetry.for_session(session, self.history)

        self._slow_at = 0.0
        self._slow_busy = False
        self._info: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        report_btn = QPushButton("Gerar relatório…")
        report_btn.setToolTip(
            "Cria um arquivo HTML autocontido com o retrato do equipamento: "
            "identificação, carga, interfaces, óptica, alarmes, vizinhos e "
            "roteamento. Abre em qualquer navegador e imprime bem.")
        report_btn.clicked.connect(self._build_report)
        bar.addWidget(report_btn)
        self.report_config_chk = QCheckBox("incluir a configuração completa")
        self.report_config_chk.setToolTip(
            "Anexa o 'display current-configuration' ao final do relatório.")
        bar.addWidget(self.report_config_chk)
        bar.addStretch(1)
        root.addLayout(bar)

        root.addWidget(self._build_cards())

        middle = QHBoxLayout()
        middle.setSpacing(6)
        middle.addWidget(self._build_top_interfaces(), 3)
        middle.addWidget(self._build_side(), 2)
        root.addLayout(middle, 1)

        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(_REFRESH_MS)

        self.engine.acquire_traffic("dashboard")
        self.engine.start()
        self.engine.poke()
        self._refresh()

    # ================================================================== #
    # Construção
    # ================================================================== #
    def _build_cards(self) -> QWidget:
        box = QWidget()
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.cpu_card = MiniChart("CPU", "%", color="#c0392b")
        self.mem_card = MiniChart("Memória", "%", color="#7d3c98")
        self.temp_card = MiniChart("Temperatura", "°C", color="#d08a1f",
                                   maximum=None)
        self.rx_card = MiniChart("Banda total — download (RX)", "",
                                 color="#2e8b57", maximum=None)
        self.tx_card = MiniChart("Banda total — upload (TX)", "",
                                 color="#1f6fb2", maximum=None)
        for card in (self.cpu_card, self.mem_card, self.temp_card,
                     self.rx_card, self.tx_card):
            layout.addWidget(card)
        return box

    def _build_top_interfaces(self) -> QGroupBox:
        box = QGroupBox("Portas com mais tráfego agora")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 4, 6, 6)
        self.top_table = QTableWidget(0, 6)
        self.top_table.setHorizontalHeaderLabels(
            ["Interface", "Estado", "RX", "TX", "Uso RX", "Uso TX"])
        self.top_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.top_table.setAlternatingRowColors(True)
        self.top_table.verticalHeader().setVisible(False)
        header = self.top_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        # As barras precisam de largura própria: com ResizeToContents elas
        # encolhiam até o texto da porcentagem sair por cima da barra.
        for column in (4, 5):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.top_table.setColumnWidth(column, 118)
        self.top_table.setItemDelegateForColumn(4, _BarDelegate(self))
        self.top_table.setItemDelegateForColumn(5, _BarDelegate(self))
        layout.addWidget(self.top_table)
        return box

    def _build_side(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.info_box = QGroupBox("Equipamento")
        self.info_grid = QGridLayout(self.info_box)
        self.info_grid.setContentsMargins(6, 4, 6, 6)
        self.info_grid.setColumnStretch(1, 1)
        self._info_labels: dict[str, QLabel] = {}
        for row, key in enumerate(
                ["Nome", "Produto", "Software", "Ligado há", "Sessão"]):
            self.info_grid.addWidget(QLabel(f"<b>{key}:</b>"), row, 0,
                                     Qt.AlignTop)
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_grid.addWidget(value, row, 1, Qt.AlignTop)
            self._info_labels[key] = value
        layout.addWidget(self.info_box)

        self.health_box = QGroupBox("Estado geral")
        health = QGridLayout(self.health_box)
        health.setContentsMargins(6, 4, 6, 6)
        health.setColumnStretch(1, 1)
        self._health_labels: dict[str, QLabel] = {}
        for row, key in enumerate(
                ["Interfaces", "Vizinhos BGP", "Vizinhos OSPF",
                 "Alarmes ativos", "Fila de comandos"]):
            health.addWidget(QLabel(f"<b>{key}:</b>"), row, 0, Qt.AlignTop)
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            health.addWidget(value, row, 1, Qt.AlignTop)
            self._health_labels[key] = value
        layout.addWidget(self.health_box)
        layout.addStretch(1)
        return box

    # ================================================================== #
    # Atualização
    # ================================================================== #
    def _refresh(self) -> None:
        if not self.isVisible():
            return
        snapshot = self.engine.snapshot()
        self._update_cards(snapshot)
        self._update_top(snapshot)
        self._update_health(snapshot)
        self._update_status(snapshot)
        if time.time() - self._slow_at > _SLOW_EVERY:
            self._fetch_slow()

    def _update_cards(self, snapshot: dict) -> None:
        since = time.time() - 900          # 15 min de sparkline
        health = snapshot["health"]

        cpu = health.get("cpu")
        self.cpu_card.set_samples(self.history.metric_samples("cpu", since),
                                  f"{cpu:.0f}%" if cpu is not None else "—")
        mem = health.get("memory")
        self.mem_card.set_samples(self.history.metric_samples("memory", since),
                                  f"{mem:.0f}%" if mem is not None else "—")
        temp = health.get("temperature")
        self.temp_card.set_samples(
            self.history.metric_samples("temperature", since),
            f"{temp:.0f} °C" if temp is not None else "—")

        rx_total, tx_total = self.engine.totals()
        self.history.add_metric("total_rx", rx_total)
        self.history.add_metric("total_tx", tx_total)
        self.rx_card.set_samples(
            self.history.metric_samples("total_rx", since),
            parsers.format_bps(rx_total))
        self.tx_card.set_samples(
            self.history.metric_samples("total_tx", since),
            parsers.format_bps(tx_total))

    def _update_top(self, snapshot: dict) -> None:
        rates = snapshot["rates"]
        meta = self.engine.interface_meta()
        ranked = sorted(rates.items(), key=lambda kv: kv[1][0] + kv[1][1],
                        reverse=True)[:_TOP_ROWS]
        self.top_table.setRowCount(len(ranked))
        for row, (name, (rx, tx)) in enumerate(ranked):
            info = meta.get(name) or {}
            speed = info.get("speed_bps") or 0
            values = [name, info.get("oper", "—"),
                      parsers.format_bps(rx), parsers.format_bps(tx)]
            for column, text in enumerate(values):
                item = self.top_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.top_table.setItem(row, column, item)
                item.setText(text)
            for column, value in ((4, rx), (5, tx)):
                item = self.top_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.top_table.setItem(row, column, item)
                if speed:
                    item.setData(Qt.UserRole, min(100.0, value / speed * 100.0))
                    item.setText("")
                else:
                    item.setData(Qt.UserRole, None)
                    item.setText("—")

    def _update_health(self, snapshot: dict) -> None:
        meta = self.engine.interface_meta()
        if meta:
            up = sum(1 for m in meta.values() if m.get("oper") == "up")
            self._health_labels["Interfaces"].setText(
                f"{up} ativas de {len(meta)}")
        stats = self.session.scheduler.stats()
        self._health_labels["Fila de comandos"].setText(
            f"{stats['depth']} na fila · {stats['executed']} executados · "
            f"{stats['cache_hits']} do cache · {stats['coalesced']} unificados"
            + (f" · {stats['dropped']} descartados"
               if stats["dropped"] else ""))
        for key in ("Vizinhos BGP", "Vizinhos OSPF", "Alarmes ativos"):
            if key in self._info:
                self._health_labels[key].setText(self._info[key])

    def _update_status(self, snapshot: dict) -> None:
        source = telemetry.SOURCE_LABELS.get(snapshot["source"],
                                             snapshot["source"])
        parts = [f"fonte {source}", f"amostra a cada {snapshot['interval']:.0f}s",
                 snapshot["status"]]
        if snapshot["at"]:
            parts.append(
                "atualizado " +
                time.strftime("%H:%M:%S", time.localtime(snapshot["at"])))
        if snapshot["error"]:
            parts.append(f"⚠ {snapshot['error']}")
        self.status.setText("  •  ".join(parts))

    # ------------------------------------------------------------------ #
    def _fetch_slow(self) -> None:
        """Consultas caras: prioridade de fundo, com cache e prazo."""
        if self._slow_busy:
            return
        self._slow_busy = True
        self._slow_at = time.time()
        session = self.session

        def query(command: str, timeout: float) -> str:
            try:
                return session.run(command, timeout=timeout,
                                   priority=BACKGROUND, ttl=_SLOW_EVERY * 0.9,
                                   max_wait=_SLOW_EVERY, tag="dashboard")
            except Exception:                     # noqa: BLE001 — vale zero
                return ""

        def fetch() -> dict:
            version = parsers.parse_version(query("display version", 20.0))
            bgp = parsers.parse_bgp_peers(query("display bgp peer", 25.0))
            ospf = parsers.parse_ospf_peers(
                query("display ospf peer brief", 20.0))
            # Alarme ATIVO é o que ainda vale; o logbuffer conta também o
            # que já foi resolvido. Se o equipamento não tiver o comando,
            # cai para a contagem de eventos graves do buffer.
            alarms = parsers.parse_active_alarms(
                query("display alarm active", 25.0))
            fallback = 0
            if not alarms:
                logs = parsers.parse_logbuffer(query("display logbuffer", 25.0))
                fallback = sum(1 for entry in logs if entry["level"] <= 4)
            return {"version": version, "bgp": bgp, "ospf": ospf,
                    "alarms": alarms, "fallback": fallback}

        def done(data: dict) -> None:
            self._slow_busy = False
            version = data["version"]
            self._info_labels["Nome"].setText(self.session.hostname or "—")
            self._info_labels["Produto"].setText(version["product"] or "—")
            self._info_labels["Software"].setText(version["software"] or "—")
            self._info_labels["Ligado há"].setText(version["uptime"] or "—")
            self._info_labels["Sessão"].setText(
                f"{self.session.username}@{self.session.host}:"
                f"{self.session.port}")
            bgp = data["bgp"]
            established = sum(1 for peer in bgp
                              if peer["state"].lower().startswith("estab"))
            self._info["Vizinhos BGP"] = (
                f"{established} estabelecidos de {len(bgp)}" if bgp
                else "sem BGP configurado")
            ospf = data["ospf"]
            full = sum(1 for peer in ospf
                       if peer["state"].lower().startswith("full"))
            self._info["Vizinhos OSPF"] = (
                f"{full} em Full de {len(ospf)}" if ospf
                else "sem OSPF configurado")
            alarms = data["alarms"]
            if alarms:
                counters: dict[int, int] = {}
                for alarm in alarms:
                    counters[alarm["level"]] = counters.get(
                        alarm["level"], 0) + 1
                detail = " · ".join(
                    f"{count} {parsers.ALARM_LABELS.get(level, level)}"
                    for level, count in sorted(counters.items()))
                self._info["Alarmes ativos"] = f"{len(alarms)} — {detail}"
            elif data["fallback"]:
                self._info["Alarmes ativos"] = (
                    f"nenhum ativo · {data['fallback']} evento(s) grave(s) "
                    f"no logbuffer")
            else:
                self._info["Alarmes ativos"] = "nenhum"

        def fail(_message: str) -> None:
            self._slow_busy = False

        run_async(fetch, done, fail)

    # ================================================================== #
    # Relatório
    # ================================================================== #
    def _build_report(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Salvar relatório",
            f"relatorio-{self.session.hostname}-{stamp}.html",
            "Página HTML (*.html);;Todos (*)")
        if not path:
            return
        include_config = self.report_config_chk.isChecked()
        self.status.setText("Coletando dados para o relatório…")
        session = self.session
        engine = self.engine
        snapshot = engine.snapshot()
        meta = engine.interface_meta()
        totals = engine.totals()

        def query(command: str, timeout: float) -> str:
            try:
                # Prioridade normal: o usuário pediu e está esperando, mas
                # não é uma configuração — não precisa furar a fila.
                return session.run(command, timeout=timeout, ttl=20.0,
                                   tag="dashboard")
            except Exception:                # noqa: BLE001 — seção some
                return ""

        def collect() -> dict:
            version = parsers.parse_version(query("display version", 20.0))
            interfaces = parsers.parse_interface_description(
                query("display interface description", 40.0))
            optics = {}
            for command in ("display transceiver diagnosis interface",
                            "display transceiver all"):
                text = query(command, 60.0)
                optics = parsers.parse_transceiver_diagnosis(text)
                if optics:
                    break
            # Aproveita os limiares que o painel Saúde das portas já
            # descobriu: sem eles o relatório sairia com "sem limiar" em
            # todas as portas, sem conseguir apontar nenhuma como crítica.
            learned = engine.optical_thresholds()
            for name, entry in optics.items():
                values = learned.get(name)
                if values:
                    entry.update(values)
                    parsers.evaluate_dom(entry)
            data = {
                "device": {
                    "hostname": session.hostname,
                    "host": f"{session.host}:{session.port}",
                    "product": version.get("product", ""),
                    "software": version.get("software", ""),
                    "uptime": version.get("uptime", ""),
                },
                "health": snapshot["health"],
                "interfaces": interfaces,
                "rates": snapshot["rates"],
                "speeds": {name: info.get("speed_bps", 0)
                           for name, info in meta.items()},
                "optics": optics,
                "alarms": parsers.parse_active_alarms(
                    query("display alarm active", 30.0)),
                "neighbors": parsers.parse_lldp_neighbors(
                    query("display lldp neighbor brief", 25.0)),
                "bgp": parsers.parse_bgp_peers(query("display bgp peer", 30.0)),
                "ospf": parsers.parse_ospf_peers(
                    query("display ospf peer brief", 25.0)),
                "totals": (parsers.format_bps(totals[0]),
                           parsers.format_bps(totals[1])),
            }
            if include_config:
                data["config"] = query("display current-configuration", 120.0)
            return data

        def done(data: dict) -> None:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(report.build(data))
            except OSError as exc:
                self.status.setText("")
                QMessageBox.warning(self, self.title,
                                    f"Falha ao gravar o relatório: {exc}")
                return
            self.status.setText(f"Relatório salvo em {path}")
            QMessageBox.information(
                self, self.title,
                f"Relatório gerado:\n{path}\n\nAbra no navegador para ver ou "
                f"imprimir.")

        def fail(message: str) -> None:
            self.status.setText("")
            QMessageBox.warning(self, self.title,
                                f"Falha ao coletar os dados: {message}")

        run_async(collect, done, fail)

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if not self._timer.isActive():
            self._timer.start(_REFRESH_MS)
        super().showEvent(event)
        # Preenche já ao aparecer: esperar o primeiro tique do timer deixava
        # a janela em branco por um segundo ao abrir.
        self._refresh()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self.engine.release_traffic("dashboard")
        self.session.scheduler.cancel("dashboard")
        super().closeEvent(event)
