"""Painel Tráfego — consumo de banda em tempo real, várias portas de uma vez.

O gráfico antigo mostrava **uma** interface por janela e fazia a própria
coleta (um ``display interface <porta>`` por amostra). Com o painel aberto
junto de outras janelas, essas consultas disputavam a sessão SSH e o
desenho engasgava.

Agora:

* a coleta é do :mod:`~huawei_winbox.core.telemetry` — um coletor só para
  toda a aplicação, que amostra **todas** as portas por vez (via GET-BULK
  quando há SNMP, senão um único ``display interface brief``);
* a lista da esquerda deixa marcar quantas interfaces quiser e mostra
  RX/TX/pico/média de cada uma, atualizados ao vivo;
* o gráfico desenha todas as marcadas juntas — sobrepostas, em grade
  (um mini-gráfico por porta) ou empilhadas;
* o cursor do mouse mostra o valor de todas elas no mesmo instante.

Marcar mais interfaces **não** aumenta a carga sobre o equipamento: os
dados de todas já vêm na mesma amostra.
"""

from __future__ import annotations

import csv
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QPushButton, QLineEdit, QSpinBox, QGroupBox, QMessageBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QToolButton, QFileDialog,
    QCheckBox,
)

from ...core import parsers, snmp, telemetry, workspace
from ...core import history as history_mod
from ..worker import run_async
from ..widgets.traffic_graph import (
    TrafficGraphWidget, Series, series_color, swatch_icon, MODES, DIRECTIONS,
    SCALES, MODE_GRID,
)

# (rótulo, segundos) — None = tudo o que foi coletado
_WINDOWS = [("30 s", 30), ("1 min", 60), ("5 min", 300), ("15 min", 900),
            ("1 hora", 3600), ("Tudo", None)]
_INTERVALS = [1, 2, 3, 5, 10, 30]
# Teto de pontos desenhados por série: acima disso a decimação entra e o
# gráfico continua fluido mesmo com uma hora de histórico e 20 portas.
_MAX_POINTS = 900
_REDRAW_MS = 500


class TrafficPanel(QWidget):
    title = "Tráfego"
    state_key = "traffic"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history = history_mod.for_session(session)
        self.engine = telemetry.for_session(session, self.history)
        # Preferências da última sessão com ESTE equipamento: quais portas
        # estavam marcadas e em que modo. Reabrir o gráfico e ter que
        # remarcar tudo era o tipo de atrito que se paga todo dia.
        self._saved = workspace.panel_state(session.host, self.state_key)

        self._colors: dict[str, int] = {}
        self._checked: set[str] = set(
            n for n in self._saved.get("checked", []) if isinstance(n, str))
        self._rows: dict[str, int] = {}
        self._known: list[str] = []
        self._last_generation = -1
        self._paused = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_controls())
        root.addWidget(self._build_snmp_box())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_side_panel())
        self.graph = TrafficGraphWidget()
        self.graph.seriesClicked.connect(self._focus_series)
        splitter.addWidget(self.graph)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 640])
        root.addWidget(splitter, 1)

        self.status = QLabel("Iniciando coleta…")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        self._restore_controls()

        # Timer só de DESENHO: lê o retrato em memória, nunca o equipamento.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_view)
        self._timer.start(_REDRAW_MS)

        self.engine.acquire_traffic("traffic-panel")
        self.engine.start()
        self.engine.poke()

    def _restore_controls(self) -> None:
        """Alinha os controles com o motor e com o que foi salvo.

        Os combos refletem o motor (que é compartilhado com o Painel e a
        barra de status) em vez de imporem os próprios padrões — senão
        abrir esta janela mudaria a coleta de todo mundo sem o usuário
        pedir.
        """
        for combo, key, setter in (
            (self.mode_combo, "mode", self.mode_combo.setCurrentText),
            (self.direction_combo, "direction",
             self.direction_combo.setCurrentText),
            (self.scale_combo, "scale", self.scale_combo.setCurrentText),
        ):
            value = self._saved.get(key)
            if isinstance(value, str) and combo.findText(value) >= 0:
                combo.blockSignals(True)
                setter(value)
                combo.blockSignals(False)
        window = self._saved.get("window")
        if window is not None or "window" in self._saved:
            index = self.window_combo.findData(window)
            if index >= 0:
                self.window_combo.setCurrentIndex(index)

        index = self.source_combo.findData(self.engine.source)
        if index >= 0:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentIndex(index)
            self.source_combo.blockSignals(False)
        index = self.interval_combo.findData(int(self.engine.interval))
        if index >= 0:
            self.interval_combo.blockSignals(True)
            self.interval_combo.setCurrentIndex(index)
            self.interval_combo.blockSignals(False)
        else:
            self.engine.set_interval(self.interval_combo.currentData())

        self.community_edit.setText(self.engine.community)
        self.port_spin.setValue(self.engine.snmp_port)
        self.snmp_box.setVisible(self.engine.source != telemetry.SOURCE_CLI)
        self.graph.set_mode(self.mode_combo.currentText())
        self.graph.set_direction(self.direction_combo.currentText())
        self.graph.set_scale(self.scale_combo.currentText())
        self.scale_combo.setEnabled(
            self.mode_combo.currentText() == MODE_GRID)

    def collect_state(self) -> dict:
        """O que vale a pena reencontrar na próxima conexão."""
        return {
            "checked": sorted(self._checked),
            "mode": self.mode_combo.currentText(),
            "direction": self.direction_combo.currentText(),
            "scale": self.scale_combo.currentText(),
            "window": self.window_combo.currentData(),
        }

    # ================================================================== #
    # Construção da interface
    # ================================================================== #
    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)

        row.addWidget(QLabel("Fonte:"))
        self.source_combo = QComboBox()
        for key in (telemetry.SOURCE_AUTO, telemetry.SOURCE_SNMP,
                    telemetry.SOURCE_CLI):
            self.source_combo.addItem(telemetry.SOURCE_LABELS[key], key)
        self.source_combo.setToolTip(
            "Automático usa SNMP quando disponível (amostra instantânea e "
            "barata) e cai para a CLI quando não há agente configurado.")
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        row.addWidget(self.source_combo)

        row.addWidget(QLabel("Intervalo:"))
        self.interval_combo = QComboBox()
        for secs in _INTERVALS:
            self.interval_combo.addItem(f"{secs}s", secs)
        self.interval_combo.setCurrentIndex(_INTERVALS.index(2))
        self.interval_combo.currentIndexChanged.connect(self._interval_changed)
        row.addWidget(self.interval_combo)

        row.addWidget(QLabel("Janela:"))
        self.window_combo = QComboBox()
        for label, secs in _WINDOWS:
            self.window_combo.addItem(label, secs)
        self.window_combo.setCurrentIndex(2)          # 5 min
        self.window_combo.currentIndexChanged.connect(self._redraw)
        row.addWidget(self.window_combo)

        row.addWidget(QLabel("Exibição:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.setToolTip(
            "Sobreposto: todas as portas no mesmo eixo.\n"
            "Grade: um mini-gráfico por porta (ótimo para muitas de uma vez).\n"
            "Empilhado: composição do tráfego total.")
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        row.addWidget(self.mode_combo)

        self.direction_combo = QComboBox()
        self.direction_combo.addItems(DIRECTIONS)
        self.direction_combo.currentTextChanged.connect(
            lambda text: (self.graph.set_direction(text), self._redraw()))
        row.addWidget(self.direction_combo)

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(SCALES)
        self.scale_combo.setToolTip(
            "Na grade, a escala comum deixa comparar portas entre si; "
            "a escala por gráfico dá zoom em cada uma.")
        self.scale_combo.currentTextChanged.connect(
            lambda text: (self.graph.set_scale(text), self._redraw()))
        self.scale_combo.setEnabled(False)
        row.addWidget(self.scale_combo)

        row.addStretch(1)

        self.pause_btn = QToolButton()
        self.pause_btn.setText("Congelar")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip(
            "Congela o desenho para examinar o gráfico. A coleta continua "
            "por trás — ao descongelar, o histórico aparece completo.")
        self.pause_btn.toggled.connect(self._toggle_pause)
        row.addWidget(self.pause_btn)

        self.run_btn = QPushButton("Parar coleta")
        self.run_btn.clicked.connect(self._toggle_running)
        row.addWidget(self.run_btn)
        return row

    def _build_snmp_box(self) -> QGroupBox:
        self.snmp_box = QGroupBox(
            "SNMP (somente leitura — o agente já deve estar habilitado "
            "no equipamento)")
        grid = QGridLayout(self.snmp_box)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.addWidget(QLabel("Comunidade:"), 0, 0)
        self.community_edit = QLineEdit("public")
        self.community_edit.setMaximumWidth(160)
        grid.addWidget(self.community_edit, 0, 1)
        grid.addWidget(QLabel("Porta UDP:"), 0, 2)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(161)
        self.port_spin.setMaximumWidth(80)
        grid.addWidget(self.port_spin, 0, 3)
        apply_btn = QPushButton("Aplicar")
        apply_btn.clicked.connect(self._apply_snmp)
        grid.addWidget(apply_btn, 0, 4)
        test_btn = QPushButton("Testar SNMP")
        test_btn.clicked.connect(self._test_snmp)
        grid.addWidget(test_btn, 0, 5)
        grid.setColumnStretch(6, 1)
        self.snmp_box.setVisible(True)
        return self.snmp_box

    def _build_side_panel(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(3)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar interfaces…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, 1)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        buttons.setSpacing(3)
        for text, slot, tip in [
            ("Ativas", self._select_active,
             "Marca as portas que estão passando tráfego agora."),
            ("Top 8", self._select_top,
             "Marca as 8 portas de maior consumo no momento."),
            ("Nenhuma", self._select_none, "Desmarca todas."),
            ("CSV…", self._export_csv,
             "Exporta o histórico das portas marcadas."),
        ]:
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.only_active_chk = QCheckBox("Ocultar portas sem tráfego")
        self.only_active_chk.toggled.connect(self._apply_filter)
        layout.addWidget(self.only_active_chk)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Interface", "Estado", "RX", "TX", "Pico"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("itemCount")
        layout.addWidget(self.count_label)
        return box

    # ================================================================== #
    # Controles
    # ================================================================== #
    def _source_changed(self) -> None:
        source = self.source_combo.currentData()
        self.engine.set_source(source)
        self.snmp_box.setVisible(source != telemetry.SOURCE_CLI)
        self.engine.poke()

    def _interval_changed(self) -> None:
        self.engine.set_interval(self.interval_combo.currentData())
        self.engine.poke()

    def _mode_changed(self, mode: str) -> None:
        self.graph.set_mode(mode)
        self.scale_combo.setEnabled(mode == MODE_GRID)
        self._redraw()

    def _toggle_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setText("Descongelar" if paused else "Congelar")
        if not paused:
            self._redraw()

    def _toggle_running(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.run_btn.setText("Iniciar coleta")
        else:
            self.engine.start()
            self.engine.poke()
            self.run_btn.setText("Parar coleta")

    def _apply_snmp(self) -> None:
        community = self.community_edit.text().strip() or "public"
        self.engine.configure_snmp(community, self.port_spin.value())
        self.engine.poke()
        # Grava na hora: é a configuração que mais dói ter que redigitar, e
        # esperar o fechamento da janela para salvar a perderia num travamento.
        workspace.save(self.session.host,
                       {"snmp_community": community,
                        "snmp_port": self.port_spin.value()})
        self.status.setText("Configuração SNMP aplicada — recoletando…")

    def _test_snmp(self) -> None:
        client = snmp.SnmpV2cClient(
            self.session.host,
            community=self.community_edit.text().strip() or "public",
            port=self.port_spin.value(), timeout=2.0, retries=1)

        def fetch():
            info = client.get([snmp.OID_SYS_NAME, snmp.OID_SYS_DESCR,
                               snmp.OID_SYS_UPTIME, snmp.OID_SYS_LOCATION])
            meta = client.interface_metadata()
            return info, meta, client.supports_bulk

        def done(data):
            info, meta, bulk = data
            ticks = info.get(snmp.OID_SYS_UPTIME)
            uptime = "—"
            if isinstance(ticks, int):
                secs = ticks // 100
                days, rem = divmod(secs, 86400)
                hours, rem = divmod(rem, 3600)
                minutes, seconds = divmod(rem, 60)
                uptime = f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
            bulk_note = ("suportado (amostragem rápida)" if bulk
                         else "não suportado — usando GET-NEXT")
            QMessageBox.information(
                self, "Teste SNMP — OK",
                f"sysName: {info.get(snmp.OID_SYS_NAME) or '—'}\n"
                f"sysLocation: {info.get(snmp.OID_SYS_LOCATION) or '—'}\n"
                f"sysUpTime: {uptime}\n"
                f"Interfaces visíveis: {len(meta)}\n"
                f"GET-BULK: {bulk_note}\n\n"
                f"sysDescr:\n{str(info.get(snmp.OID_SYS_DESCR) or '—')[:400]}")

        def fail(message: str):
            QMessageBox.warning(self, "Teste SNMP — falhou", str(message))

        self.status.setText("Testando SNMP…")
        run_async(fetch, done, fail)

    # ================================================================== #
    # Seleção de interfaces
    # ================================================================== #
    def _color_index(self, name: str) -> int:
        index = self._colors.get(name)
        if index is None:
            index = len(self._colors)
            self._colors[name] = index
        return index

    def _sync_table(self, names: list[str]) -> None:
        """Recria as linhas só quando a lista de interfaces muda."""
        if names == self._known:
            return
        self._known = names
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._rows = {}
        for name in names:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in self._checked
                               else Qt.Unchecked)
            # O quadradinho é o que liga cada linha do gráfico à sua porta.
            item.setIcon(swatch_icon(series_color(self._color_index(name))))
            self.table.setItem(row, 0, item)
            for column in range(1, 5):
                self.table.setItem(row, column, QTableWidgetItem(""))
            self._rows[name] = row
        self.table.blockSignals(False)
        self._apply_filter()

    def _item_changed(self, item) -> None:
        if item.column() != 0:
            return
        name = item.text()
        if item.checkState() == Qt.Checked:
            self._checked.add(name)
        else:
            self._checked.discard(name)
        self._redraw()

    def _selection_changed(self) -> None:
        """Realça no gráfico a linha selecionada na tabela."""
        self.graph.set_highlight(item.text() for item in
                                 self.table.selectedItems()
                                 if item.column() == 0)

    def _focus_series(self, key: str) -> None:
        row = self._rows.get(key)
        if row is not None:
            self.table.selectRow(row)

    def _set_checked(self, names) -> None:
        self._checked = set(names)
        self.table.blockSignals(True)
        for name, row in self._rows.items():
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if name in self._checked
                                   else Qt.Unchecked)
        self.table.blockSignals(False)
        self._redraw()

    def _select_active(self) -> None:
        snapshot = self.engine.snapshot()
        active = [name for name, (rx, tx) in snapshot["rates"].items()
                  if rx > 1000 or tx > 1000]
        if not active:
            active = list(snapshot["rates"])[:8]
        self._set_checked(active[:24])

    def _select_top(self) -> None:
        rates = self.engine.snapshot()["rates"]
        top = sorted(rates.items(), key=lambda kv: kv[1][0] + kv[1][1],
                     reverse=True)[:8]
        self._set_checked(name for name, _pair in top)

    def _select_none(self) -> None:
        self._set_checked([])

    def focus_payload(self, names) -> None:
        """Marca as interfaces pedidas por outro painel ("Ver no gráfico")."""
        wanted = [names] if isinstance(names, str) else list(names)
        if not wanted:
            return
        # A porta pode ainda não estar na tabela (o coletor acabou de subir):
        # garante a linha antes de marcar.
        known = self.engine.interface_names() or self.history.interfaces()
        self._sync_table(sorted(set(known) | set(wanted)))
        self._set_checked(wanted)
        self.filter_edit.clear()
        row = self._rows.get(wanted[0])
        if row is not None:
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        only_active = self.only_active_chk.isChecked()
        rates = self.engine.snapshot()["rates"]
        shown = 0
        for name, row in self._rows.items():
            visible = (not text) or text in name.lower()
            if visible and only_active and name not in self._checked:
                pair = rates.get(name)
                visible = bool(pair and (pair[0] > 1000 or pair[1] > 1000))
            self.table.setRowHidden(row, not visible)
            shown += int(visible)
        self.count_label.setText(
            f"{len(self._checked)} marcada(s) · {shown} de "
            f"{len(self._rows)} interface(s)")

    # ================================================================== #
    # Atualização da tela
    # ================================================================== #
    def _refresh_view(self) -> None:
        if not self.isVisible():
            return
        snapshot = self.engine.snapshot()
        names = self.engine.interface_names() or self.history.interfaces()
        self._sync_table(names)
        if snapshot["generation"] != self._last_generation:
            self._last_generation = snapshot["generation"]
            self._update_table(snapshot)
            if not self._paused:
                self._redraw()
        self._update_status(snapshot)

    def _window_start(self) -> float | None:
        seconds = self.window_combo.currentData()
        return None if seconds is None else time.time() - seconds

    def _update_table(self, snapshot: dict) -> None:
        rates = snapshot["rates"]
        since = self._window_start()
        self.table.blockSignals(True)
        meta = self.engine.interface_meta()
        for name, row in self._rows.items():
            pair = rates.get(name)
            series = self.history.series(name)
            state = (meta.get(name) or {}).get("oper", "")
            self.table.item(row, 1).setText(state)
            if pair:
                self.table.item(row, 2).setText(parsers.format_bps(pair[0]))
                self.table.item(row, 3).setText(parsers.format_bps(pair[1]))
            if series is not None:
                peak_rx, peak_tx = series.peak(since)
                self.table.item(row, 4).setText(
                    parsers.format_bps(max(peak_rx, peak_tx)))
        self.table.blockSignals(False)

    def _redraw(self, *_args) -> None:
        since = self._window_start()
        # Fora do laço: interface_meta() copia a tabela inteira, e chamá-la
        # por interface marcada custava dezenas de cópias por segundo.
        meta = self.engine.interface_meta()
        # Nunca mais pontos do que pixels: uma hora de histórico a 1 s são
        # 3600 amostras por porta, e desenhar isso vezes 20 portas numa área
        # de 800 px é trabalho invisível.
        budget = max(200, min(_MAX_POINTS, self.graph.width() or _MAX_POINTS))
        series_list = []
        for name in sorted(self._checked):
            samples = self.history.samples(name, since, budget)
            if not samples:
                continue
            info = meta.get(name) or {}
            series_list.append(Series(
                key=name, label=name,
                color=series_color(self._color_index(name)),
                samples=samples, speed_bps=info.get("speed_bps", 0)))
        self.graph.set_window(self.window_combo.currentData())
        self.graph.set_series(series_list)
        total_rx = sum(s.samples[-1][1] for s in series_list if s.samples)
        total_tx = sum(s.samples[-1][2] for s in series_list if s.samples)
        if series_list:
            self.graph.set_title(
                f"{len(series_list)} interface(s) — "
                f"total ↓ {parsers.format_bps(total_rx)}  "
                f"↑ {parsers.format_bps(total_tx)}",
                "RX em linha cheia, TX tracejado · passe o mouse para ler "
                "todos os valores no mesmo instante")
        else:
            self.graph.set_title("")
        self._selection_changed()
        self._apply_filter()

    def _update_status(self, snapshot: dict) -> None:
        parts = [telemetry.SOURCE_LABELS.get(snapshot["source"],
                                             snapshot["source"]),
                 f"{snapshot['interval']:.0f}s",
                 snapshot["status"],
                 f"{snapshot['interfaces']} interface(s) amostradas"]
        if snapshot["at"]:
            age = time.time() - snapshot["at"]
            parts.append(f"última amostra há {age:.0f}s")
        stats = self.session.scheduler.stats()
        if stats["depth"]:
            parts.append(f"fila SSH: {stats['depth']}")
        if snapshot["error"]:
            parts.append(f"⚠ {snapshot['error']}")
        self.status.setText("  •  ".join(parts))

    # ================================================================== #
    def _export_csv(self) -> None:
        if not self._checked:
            QMessageBox.information(
                self, self.title,
                "Marque ao menos uma interface para exportar.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Exportar histórico",
            f"trafego-{self.session.hostname}.csv", "CSV (*.csv)")
        if not path:
            return
        since = self._window_start()
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["horario", "interface", "rx_bps", "tx_bps"])
                for name in sorted(self._checked):
                    for t, rx, tx in self.history.samples(name, since):
                        writer.writerow([
                            time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(t)),
                            name, f"{rx:.0f}", f"{tx:.0f}"])
        except OSError as exc:
            QMessageBox.warning(self, self.title, f"Falha ao gravar: {exc}")
            return
        self.status.setText(f"Histórico exportado para {path}")

    # ------------------------------------------------------------------ #
    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if not self._timer.isActive():
            self._timer.start(_REDRAW_MS)
        super().showEvent(event)
        self._refresh_view()

    def closeEvent(self, event) -> None:
        # O motor NÃO para: outras janelas (o Painel, a barra de status)
        # continuam usando o mesmo coletor, e o histórico segue completo se
        # esta janela for reaberta. Só deixamos de pedir as séries de
        # tráfego — se ninguém mais quiser, ele volta a só olhar a carga.
        self._timer.stop()
        self.engine.release_traffic("traffic-panel")
        super().closeEvent(event)
