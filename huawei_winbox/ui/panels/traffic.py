"""Painel Tráfego — gráfico de consumo de banda em tempo real.

Coleta a banda (RX/TX) da interface selecionada enquanto a janela está
aberta e desenha o histórico num gráfico (estilo *Traffic* do WinBox). Há
duas fontes de dados:

* **CLI (SSH)** — lê a taxa informada pelo próprio equipamento
  (``display interface <nome>``); funciona sem configurar nada além do SSH.
* **SNMP** — lê os contadores de octetos (ifHCInOctets/ifHCOutOctets) e
  calcula a taxa entre as amostras; exige que o agente SNMP já esteja
  habilitado no equipamento (a comunidade é informada aqui).

O histórico fica em memória, vinculado à sessão, e sobrevive ao fechar e
reabrir o painel (``core.history``).
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QPushButton, QLineEdit, QSpinBox, QGroupBox, QMessageBox,
)

from ...core import parsers, snmp
from ...core import history as history_mod
from ..worker import run_async
from ..widgets.traffic_graph import TrafficGraphWidget

# (rótulo, segundos) — None = mostrar tudo o que foi coletado
_WINDOWS = [("1 min", 60), ("5 min", 300), ("15 min", 900),
            ("1 hora", 3600), ("Tudo", None)]
_INTERVALS = [2, 3, 5, 10, 30]
_MAX_FAILS = 3        # falhas consecutivas antes de pausar a coleta


class TrafficPanel(QWidget):
    title = "Tráfego"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history = history_mod.for_session(session)

        self._busy = False
        self._running = False
        self._fail_count = 0
        self._source = "CLI (SSH)"
        self._snmp_index: dict[str, int] = {}
        self._snmp_prev: dict[str, tuple[float, int, int]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ── Linha 1: interface, fonte, intervalo, janela, start/stop ──── #
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(QLabel("Interface:"))
        self.iface_combo = QComboBox()
        self.iface_combo.setMinimumWidth(220)
        self.iface_combo.currentIndexChanged.connect(self._iface_changed)
        row1.addWidget(self.iface_combo, 1)

        row1.addWidget(QLabel("Fonte:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["CLI (SSH)", "SNMP"])
        self.source_combo.currentTextChanged.connect(self._source_changed)
        row1.addWidget(self.source_combo)

        row1.addWidget(QLabel("Intervalo:"))
        self.interval_combo = QComboBox()
        for s in _INTERVALS:
            self.interval_combo.addItem(f"{s}s", s)
        self.interval_combo.setCurrentIndex(_INTERVALS.index(5))
        self.interval_combo.currentIndexChanged.connect(self._interval_changed)
        row1.addWidget(self.interval_combo)

        row1.addWidget(QLabel("Janela:"))
        self.window_combo = QComboBox()
        for label, secs in _WINDOWS:
            self.window_combo.addItem(label, secs)
        self.window_combo.setCurrentIndex(1)   # 5 min
        self.window_combo.currentIndexChanged.connect(lambda *_: self._redraw())
        row1.addWidget(self.window_combo)

        self.start_btn = QPushButton("Iniciar")
        self.start_btn.clicked.connect(self._toggle_running)
        row1.addWidget(self.start_btn)
        root.addLayout(row1)

        # ── Linha 2 (SNMP): comunidade, porta, testar ─────────────────── #
        self.snmp_box = QGroupBox("SNMP (somente leitura — o agente já deve "
                                  "estar habilitado no equipamento)")
        snmp_grid = QGridLayout(self.snmp_box)
        snmp_grid.addWidget(QLabel("Comunidade:"), 0, 0)
        self.community_edit = QLineEdit("public")
        snmp_grid.addWidget(self.community_edit, 0, 1)
        snmp_grid.addWidget(QLabel("Porta UDP:"), 0, 2)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(161)
        snmp_grid.addWidget(self.port_spin, 0, 3)
        test_btn = QPushButton("Testar SNMP")
        test_btn.clicked.connect(self._test_snmp)
        snmp_grid.addWidget(test_btn, 0, 4)
        reload_btn = QPushButton("Recarregar interfaces")
        reload_btn.clicked.connect(lambda: self._load_interfaces(restart=True))
        snmp_grid.addWidget(reload_btn, 0, 5)
        snmp_grid.setColumnStretch(1, 1)
        self.snmp_box.setVisible(False)
        root.addWidget(self.snmp_box)

        # ── Gráfico ───────────────────────────────────────────────────── #
        self.graph = TrafficGraphWidget()
        root.addWidget(self.graph, 1)

        # ── Status ────────────────────────────────────────────────────── #
        self.status = QLabel("Carregando interfaces...")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        # Timer de coleta
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._load_interfaces(restart=True)

    # ================================================================== #
    # Interfaces / fonte
    # ================================================================== #
    @property
    def _interval(self) -> int:
        return self.interval_combo.currentData() or 5

    @property
    def _iface(self) -> str:
        return self.iface_combo.currentText().strip()

    def _make_snmp_client(self) -> snmp.SnmpV2cClient:
        return snmp.SnmpV2cClient(
            self.session.host,
            community=self.community_edit.text().strip() or "public",
            port=self.port_spin.value(),
            timeout=2.0, retries=1)

    def _load_interfaces(self, restart: bool = False) -> None:
        """Busca a lista de interfaces conforme a fonte selecionada."""
        self._set_running(False)
        self.status.setText("Carregando interfaces...")
        source = self.source_combo.currentText()
        keep = self._iface

        if source == "SNMP":
            client = self._make_snmp_client()

            def fetch():
                return client.iface_index_map()

            def done(mapping: dict):
                self._snmp_index = mapping
                names = list(mapping.keys())
                self._fill_iface_combo(names, keep)
                if not names:
                    self.status.setText(
                        "SNMP não retornou interfaces. Verifique a comunidade "
                        "e se o agente está habilitado.")
                    return
                if restart:
                    self._set_running(True)

            def fail(msg: str):
                self.status.setText(f"SNMP: {msg}")

            run_async(fetch, done, fail)
        else:
            def fetch():
                return parsers.parse_interface_description(
                    self.session.run("display interface description",
                                     timeout=30.0))

            def done(rows: list):
                names = [r["name"] for r in rows]
                # Seleciona por padrão a primeira interface fisicamente UP.
                default = next((r["name"] for r in rows
                                if "up" in r["phy"].lower()), None)
                self._fill_iface_combo(names, keep or default)
                if names and restart:
                    self._set_running(True)

            def fail(msg: str):
                self.status.setText(f"Erro ao listar interfaces: {msg}")

            run_async(fetch, done, fail)

    def _fill_iface_combo(self, names: list[str], select: str | None) -> None:
        self.iface_combo.blockSignals(True)
        self.iface_combo.clear()
        self.iface_combo.addItems(names)
        if select and select in names:
            self.iface_combo.setCurrentText(select)
        self.iface_combo.blockSignals(False)
        self._redraw()

    def _source_changed(self, source: str) -> None:
        self._source = source
        self.snmp_box.setVisible(source == "SNMP")
        self._snmp_prev.clear()
        self._load_interfaces(restart=True)

    def _iface_changed(self, *_) -> None:
        # Nova interface: zera a linha de base do SNMP e mostra o histórico
        # já coletado para ela (se houver).
        self._snmp_prev.pop(self._iface, None)
        self._fail_count = 0
        self._redraw()

    def _interval_changed(self, *_) -> None:
        if self._running and self.isVisible():
            self._timer.start(self._interval * 1000)

    # ================================================================== #
    # Coleta
    # ================================================================== #
    def _toggle_running(self) -> None:
        self._set_running(not self._running)

    def _set_running(self, running: bool) -> None:
        # _running guarda a INTENÇÃO; o timer só roda enquanto o painel está
        # visível (pausa ao minimizar/ocultar, retoma ao reexibir).
        self._running = running
        self.start_btn.setText("Parar" if running else "Iniciar")
        if running and self._iface and self.isVisible():
            self._fail_count = 0
            self._timer.start(self._interval * 1000)
            self._tick()                       # primeira amostra imediata
        else:
            self._timer.stop()

    def _tick(self) -> None:
        if self._busy or not self._running:
            return
        iface = self._iface
        if not iface:
            return
        self._busy = True
        if self._source == "SNMP":
            self._tick_snmp(iface)
        else:
            self._tick_cli(iface)

    # -- CLI --------------------------------------------------------------- #
    def _tick_cli(self, iface: str) -> None:
        def fetch():
            text = self.session.run(f"display interface {iface}", timeout=20.0)
            return parsers.parse_interface_rates_bps(text)

        def done(rates: dict):
            self._busy = False
            pair = rates.get(iface)
            if pair is None and len(rates) == 1:
                pair = next(iter(rates.values()))
            if pair is None:
                self._note_fail("equipamento não reportou taxa para a interface")
                return
            self._fail_count = 0
            self.history.add(iface, pair[0], pair[1])
            self._redraw()

        def fail(msg: str):
            self._busy = False
            self._note_fail(msg)

        run_async(fetch, done, fail)

    # -- SNMP -------------------------------------------------------------- #
    def _tick_snmp(self, iface: str) -> None:
        index = self._snmp_index.get(iface)
        if index is None:
            self._busy = False
            self._note_fail("interface não encontrada via SNMP "
                            "(use Recarregar interfaces)")
            return
        client = self._make_snmp_client()

        def fetch():
            in_oct, out_oct = client.octet_counters(index)
            return time.time(), in_oct, out_oct

        def done(sample: tuple):
            self._busy = False
            self._fail_count = 0
            t_now, in_now, out_now = sample
            prev = self._snmp_prev.get(iface)
            self._snmp_prev[iface] = sample
            if prev is None:
                self.status.setText(
                    f"SNMP • coletando linha de base de {iface}...")
                return
            t_prev, in_prev, out_prev = prev
            dt = t_now - t_prev
            if dt <= 0 or in_now < in_prev or out_now < out_prev:
                # Intervalo inválido ou contador reiniciado/voltou: descarta.
                return
            rx = (in_now - in_prev) * 8.0 / dt
            tx = (out_now - out_prev) * 8.0 / dt
            self.history.add(iface, rx, tx, when=t_now)
            self._redraw()

        def fail(msg: str):
            self._busy = False
            self._note_fail(msg)

        run_async(fetch, done, fail)

    def _note_fail(self, msg: str) -> None:
        self._fail_count += 1
        if self._fail_count >= _MAX_FAILS:
            self._set_running(False)
            self.status.setText(f"Coleta pausada após {self._fail_count} "
                                f"falhas: {msg}")
        else:
            self.status.setText(f"Aviso ({self._fail_count}/{_MAX_FAILS}): "
                                f"{msg}")

    # ================================================================== #
    # Desenho / status
    # ================================================================== #
    def _redraw(self) -> None:
        iface = self._iface
        samples = self.history.samples(iface)
        window = self.window_combo.currentData()
        self.graph.set_title(f"{iface}  ·  {self._source}" if iface else "")
        self.graph.set_samples(samples, window)
        self._update_status(samples)

    def _update_status(self, samples: list) -> None:
        if self._fail_count:
            return                          # mantém o aviso de falha visível
        state = "coletando" if self._running else "parado"
        parts = [f"{self._source}", f"{self._interval}s", state,
                 f"{len(samples)} amostra(s)"]
        if samples:
            elapsed = int(samples[-1][0] - samples[0][0])
            parts.append(f"janela {elapsed // 60:02d}:{elapsed % 60:02d}")
            parts.append(f"RX {parsers.format_bps(samples[-1][1])} / "
                         f"TX {parsers.format_bps(samples[-1][2])}")
        self.status.setText("  •  ".join(parts))

    def _test_snmp(self) -> None:
        client = self._make_snmp_client()

        def fetch():
            return client.get([snmp.OID_SYS_NAME, snmp.OID_SYS_DESCR,
                               snmp.OID_SYS_UPTIME, snmp.OID_SYS_LOCATION])

        def done(data: dict):
            name = data.get(snmp.OID_SYS_NAME) or "—"
            descr = (data.get(snmp.OID_SYS_DESCR) or "—")
            ticks = data.get(snmp.OID_SYS_UPTIME)
            loc = data.get(snmp.OID_SYS_LOCATION) or "—"
            uptime = "—"
            if isinstance(ticks, int):
                secs = ticks // 100
                d, rem = divmod(secs, 86400)
                h, rem = divmod(rem, 3600)
                m, s = divmod(rem, 60)
                uptime = f"{d}d {h:02d}:{m:02d}:{s:02d}"
            QMessageBox.information(
                self, "Teste SNMP — OK",
                f"sysName: {name}\nsysLocation: {loc}\n"
                f"sysUpTime: {uptime}\n\nsysDescr:\n{str(descr)[:400]}")

        def fail(msg: str):
            QMessageBox.warning(self, "Teste SNMP — falhou", str(msg))

        self.status.setText("Testando SNMP...")
        run_async(fetch, done, fail)

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        # Minimizar/ocultar pausa a coleta sem perder a intenção (_running)
        # nem o histórico (guardado na sessão).
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if self._running and self._iface and not self._timer.isActive():
            self._timer.start(self._interval * 1000)
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._running = False
        super().closeEvent(event)
