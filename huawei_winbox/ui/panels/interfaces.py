"""Painel de Interfaces — equivalente à janela *Interfaces* do WinBox.

Usa ``display interface description`` para mostrar os nomes reais
configurados nas portas. Duplo clique abre os detalhes completos da
porta (velocidade, duplex, negociação, potência ótica, contadores).
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QPushButton, QMessageBox, QDialog, QVBoxLayout, QGridLayout,
    QLabel, QDialogButtonBox, QGroupBox, QScrollArea, QWidget,
)

from ...core import history as history_mod
from ...core import parsers, telemetry, translator
from ..forms import SimpleForm
from ..widgets.traffic_graph import TrafficGraphWidget, Series, series_color
from ..worker import run_async
from .base import BasePanel


class InterfaceDetailDialog(QDialog):
    """Diálogo com detalhes completos da porta (double-click).

    Junta num lugar só o que estava espalhado: estado, camada física,
    diagnóstico do módulo óptico, contadores — e o **gráfico ao vivo** da
    própria porta, alimentado pelo coletor que já está amostrando. Não
    custa nenhuma consulta extra ao equipamento.
    """

    def __init__(self, iface_name: str, info: dict, parent=None,
                 history=None):
        super().__init__(parent)
        self.setWindowTitle(f"Detalhes — {iface_name}")
        self.resize(620, 620)
        self._iface = iface_name
        self._history = history
        root = QVBoxLayout(self)

        # ── Gráfico ao vivo da porta ── #
        self._graph = None
        if history is not None and history.series(iface_name) is not None:
            self._graph = TrafficGraphWidget()
            self._graph.setMinimumHeight(150)
            self._graph.setMaximumHeight(190)
            self._graph.set_window(300)
            self._graph.set_title(f"Banda de {iface_name} — últimos 5 min")
            root.addWidget(self._graph)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._update_graph)
            self._timer.start(1000)
            self._update_graph()

        # Área rolável para caber em todas as telas
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        layout = QVBoxLayout(content)
        layout.setSpacing(6)

        def grp(title: str, pairs: list[tuple[str, str]]) -> QGroupBox:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setColumnStretch(1, 1)
            for row, (label, value) in enumerate(pairs):
                lbl = QLabel(f"<b>{label}:</b>")
                lbl.setMinimumWidth(160)
                val = QLabel(value or "—")
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setWordWrap(True)
                grid.addWidget(lbl, row, 0, Qt.AlignTop)
                grid.addWidget(val, row, 1, Qt.AlignTop)
            return box

        # ── Estado / protocolo ── #
        state_up = "UP" in info["state"].upper()
        cor = "#2e8b57" if state_up else "#c0392b"
        status_lbl = QLabel(
            f'<b>Estado físico:</b> '
            f'<span style="color:{cor};font-weight:bold">{info["state"] or "—"}</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<b>Protocolo:</b> '
            f'<span style="color:{cor};font-weight:bold">{info["protocol"] or "—"}</span>'
        )
        layout.addWidget(status_lbl)

        if info["description"]:
            layout.addWidget(
                QLabel(f'<b>Descrição:</b>  {info["description"]}'))

        # ── Camada física ── #
        phys_pairs = [
            ("Velocidade",    info["speed"]),
            ("Duplex",        info["duplex"]),
            ("Negociação",    info["negotiation"]),
            ("MTU",           info["mtu"]),
        ]
        # Filtrar campos com valor
        phys_pairs = [(k, v) for k, v in phys_pairs if v]
        if phys_pairs:
            layout.addWidget(grp("Camada física", phys_pairs))

        # ── Módulo óptico (SFP) — só mostra se houver dados ── #
        optical_pairs = [
            ("Tipo de transceptor",   info["transceiver_type"]),
            ("Fabricante",            info.get("vendor", "")),
            ("Modelo (part number)",  info.get("part_number", "")),
            ("Comprimento de onda",   info.get("wavelength", "")),
            ("TX Power",              info["tx_power"]),
            ("RX Power",              info["rx_power"]),
            ("Corrente do laser",     info["bias_current"]),
            ("Temperatura do módulo", info["temperature"]),
        ]
        optical_pairs = [(k, v) for k, v in optical_pairs if v]
        if optical_pairs:
            layout.addWidget(grp("Módulo óptico (SFP/SFP+)", optical_pairs))

        # ── Tráfego e utilização ── #
        traffic_pairs = [
            ("Utilização de entrada (RX)", info["input_rate"]),
            ("Utilização de saída (TX)",   info["output_rate"]),
        ]
        traffic_pairs = [(k, v) for k, v in traffic_pairs if v]
        if traffic_pairs:
            layout.addWidget(grp("Tráfego", traffic_pairs))

        # ── Contadores de erros ── #
        err_pairs = [
            ("Erros de entrada",   info["input_errors"]),
            ("Erros de saída",     info["output_errors"]),
        ]
        err_pairs = [(k, v) for k, v in err_pairs if v]
        if err_pairs:
            layout.addWidget(grp("Erros", err_pairs))

        layout.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Close).setText("Fechar")
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _update_graph(self) -> None:
        if self._graph is None or self._history is None:
            return
        samples = self._history.samples(self._iface, time.time() - 300, 600)
        if not samples:
            return
        self._graph.set_series([Series(
            key=self._iface, label=self._iface,
            color=series_color(0), samples=samples)])


class InterfacesPanel(BasePanel):
    title = "Interfaces"
    columns = ["Interface", "Físico", "Protocolo", "RX", "TX", "Descrição"]

    # Pedido de abrir outro módulo (a MainWindow conecta se existir).
    open_panel_requested = Signal(str, object)

    def __init__(self, session, parent=None):
        # O coletor precisa existir ANTES do super().__init__(), que já
        # dispara o primeiro fetch().
        self.engine = telemetry.for_session(session)
        self._rates_generation = -1
        super().__init__(session, parent)
        # Conecta uma única vez — não em _on_rows, que roda a cada refresh.
        self.table.itemDoubleClicked.connect(self._show_detail_from_item)
        self.engine.acquire_traffic("interfaces-panel")
        self.engine.start()
        # As colunas RX/TX passam a andar sozinhas, lendo o retrato em
        # memória: banda ao vivo sem custar uma consulta sequer.
        self._rate_timer = QTimer(self)
        self._rate_timer.timeout.connect(self._update_rates)
        self._rate_timer.start(1000)

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar", lambda: self._toggle(True)),
            ("Desabilitar", lambda: self._toggle(False)),
            ("Descrição...", self._edit_description),
            ("Endereço IP...", self._add_ip),
            ("MTU...", self._set_mtu),
            ("Velocidade/Duplex...", self._speed_duplex),
            ("L2↔L3...", self._portswitch),
            ("Loopback...", self._loopback),
            ("Eth-Trunk...", self._eth_trunk),
            ("Sub-interface...", self._subinterface),
            ("Detalhes", self._show_detail),
            ("Ver no gráfico", self._show_in_graph),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def focus_payload(self, payload) -> None:
        """Filtra a tabela numa porta pedida por outro painel."""
        name = payload[0] if isinstance(payload, (list, tuple)) else payload
        if not name:
            return
        self.find_edit.setText(str(name))
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.text() == str(name):
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                break

    def _show_in_graph(self):
        """Abre o Tráfego já com as portas selecionadas marcadas."""
        names = sorted({item.text() for item in self.table.selectedItems()
                        if item.column() == 0})
        if not names:
            QMessageBox.information(
                self, self.title,
                "Selecione uma ou mais interfaces na tabela.")
            return
        self.open_panel_requested.emit("Tráfego (gráfico)", names)

    def fetch(self):
        rows = parsers.parse_interface_description(
            self.session.run("display interface description"))
        # RX/TX vêm do coletor, que já amostra todas as portas — antes esta
        # tabela baixava o dump completo do "display interface" (dezenas de
        # segundos numa placa cheia) a cada atualização, e era o comando
        # mais caro do aplicativo. Só se o coletor ainda não tiver dados é
        # que se busca algo, e aí o "brief", que é barato.
        rates = self.engine.snapshot()["rates"]
        if not rates:
            try:
                brief = parsers.parse_interface_brief(
                    self.session.run("display interface brief", timeout=30.0))
                for row in brief:
                    row_name = row["name"]
                    rows_by_name = {r["name"]: r for r in rows}
                    if row_name in rows_by_name:
                        rows_by_name[row_name]["rx"] = row["in_util"]
                        rows_by_name[row_name]["tx"] = row["out_util"]
            except Exception:                # noqa: BLE001 — coluna opcional
                pass
            for r in rows:
                r.setdefault("rx", "")
                r.setdefault("tx", "")
            return rows
        for r in rows:
            pair = rates.get(r["name"])
            r["rx"] = parsers.format_bps(pair[0]) if pair else ""
            r["tx"] = parsers.format_bps(pair[1]) if pair else ""
        return rows

    # ------------------------------------------------------------------ #
    def _update_rates(self) -> None:
        """Atualiza só as colunas RX/TX a partir do coletor (sem I/O)."""
        if not self.isVisible() or self._busy:
            return
        snapshot = self.engine.snapshot()
        if snapshot["generation"] == self._rates_generation:
            return
        self._rates_generation = snapshot["generation"]
        rates = snapshot["rates"]
        if not rates:
            return
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            if name_item is None:
                continue
            pair = rates.get(name_item.text())
            if pair is None:
                continue
            for column, value in ((3, pair[0]), (4, pair[1])):
                item = self.table.item(row, column)
                if item is not None:
                    item.setText(parsers.format_bps(value))

    def row_values(self, row):
        return [row["name"], row["phy"], row["protocol"],
                row.get("rx", ""), row.get("tx", ""), row["description"]]

    def _on_rows(self, rows):
        super()._on_rows(rows)
        # Colorir linhas de interfaces down
        for r in range(self.table.rowCount()):
            phy = self.table.item(r, 1)
            if phy and "down" in phy.text().lower():
                for c in range(self.table.columnCount()):
                    item = self.table.item(r, c)
                    if item:
                        item.setForeground(Qt.darkGray)

    def _show_detail_from_item(self, item):
        row = item.data(Qt.UserRole)
        if row:
            self._open_detail(row)

    # ----------------------------------------------------------------- #
    def _need_selection(self) -> dict | None:
        row = self.selected_row()
        if not row:
            QMessageBox.information(
                self, self.title, "Selecione uma interface na tabela.")
        return row

    def _toggle(self, enable: bool):
        row = self._need_selection()
        if row:
            self.apply_commands(
                translator.interface_shutdown(row["name"], enable))

    def _edit_description(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Descrição — {row['name']}",
            [("description", "Descrição", "text", row["description"])],
            self)
        if values is not None:
            self.apply_commands(translator.interface_description(
                row["name"], values["description"]))

    def _add_ip(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Endereço IP — {row['name']}",
            [("address", "Endereço IP", "text", ""),
             ("mask", "Máscara (ex.: 255.255.255.0 ou 24)", "text", ""),
             ("remove", "Remover este endereço", "bool", False)],
            self)
        if values and values["address"] and values["mask"]:
            self.apply_commands(translator.interface_ip(
                row["name"], values["address"], values["mask"],
                remove=values["remove"]))

    def _set_mtu(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"MTU — {row['name']}",
            [("mtu", "MTU", "int", 1500)], self)
        if values and values["mtu"]:
            self.apply_commands(
                translator.interface_mtu(row["name"], values["mtu"]))

    def _speed_duplex(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Velocidade/Duplex — {row['name']}",
            [("speed", "Velocidade", "choice:auto|10|100|1000", "auto"),
             ("duplex", "Duplex", "choice:auto|full|half", "auto")],
            self)
        if values:
            cmds = translator.interface_speed(row["name"], values["speed"])
            cmds += translator.interface_duplex(row["name"], values["duplex"])
            self.apply_commands(cmds)

    def _portswitch(self):
        row = self._need_selection()
        if not row:
            return
        values = SimpleForm.ask(
            f"Modo L2/L3 — {row['name']}",
            [("mode", "Modo", "choice:L2 (portswitch)|L3 (undo portswitch)", "L2 (portswitch)")],
            self)
        if values:
            self.apply_commands(translator.interface_portswitch(
                row["name"], to_layer2="L2" in values["mode"]))

    def _loopback(self):
        values = SimpleForm.ask(
            "Criar interface Loopback",
            [("id", "Número do LoopBack", "int", 0),
             ("ip", "Endereço IP (opcional)", "text", ""),
             ("mask", "Máscara (opcional)", "text", "")],
            self)
        if values is not None:
            self.apply_commands(translator.interface_loopback_create(
                values["id"], values["ip"], values["mask"]))

    def _eth_trunk(self):
        values = SimpleForm.ask(
            "Eth-Trunk (LAG)",
            [("action", "Ação", "choice:Criar Trunk|Adicionar membro|Remover membro|Apagar Trunk", "Criar Trunk"),
             ("trunk_id", "ID do Eth-Trunk", "int", 1),
             ("mode", "Modo (só para Criar)", "choice:lacp-static|lacp-dynamic|manual", "lacp-static"),
             ("interface", "Interface membro (para Adicionar/Remover)", "text", "")],
            self)
        if not values:
            return
        action = values["action"]
        tid = values["trunk_id"]
        if action == "Criar Trunk":
            self.apply_commands(translator.eth_trunk_create(tid, values["mode"]))
        elif action == "Apagar Trunk":
            self.apply_commands(translator.eth_trunk_delete(tid))
        elif action == "Adicionar membro" and values["interface"]:
            self.apply_commands(translator.eth_trunk_add_member(
                values["interface"], tid))
        elif action == "Remover membro" and values["interface"]:
            self.apply_commands(translator.eth_trunk_add_member(
                values["interface"], tid, remove=True))

    def _subinterface(self):
        values = SimpleForm.ask(
            "Sub-interface Dot1Q",
            [("interface", "Interface (ex.: GigabitEthernet0/0/1.10)", "text", ""),
             ("vlan_id", "VLAN ID", "int", 10),
             ("remove", "Remover sub-interface", "bool", False)],
            self)
        if values and values["interface"]:
            self.apply_commands(translator.subinterface_dot1q_simple(
                values["interface"], values["vlan_id"],
                remove=values["remove"]))

    def _show_detail(self):
        row = self._need_selection()
        if row:
            self._open_detail(row)

    def _open_detail(self, row: dict):
        self._set_busy(True, f"Consultando {row['name']}...")

        def fetch():
            # Timeout curto (10 s): display interface deve ser quase imediato.
            # Se o nome abreviado não for reconhecido, VRP retorna erro com
            # prompt — o texto vem como raw e o diálogo ainda mostra algo útil.
            try:
                text = self.session.run(
                    f"display interface {row['name']}", timeout=10.0)
            except Exception as exc:
                text = f"[Erro ao consultar interface: {exc}]"
            info = parsers.parse_interface_detail(text)
            # Potência ótica via display transceiver — só faz sentido em
            # interfaces físicas reais (não subinterfaces .X, não Vlanif,
            # não LoopBack, NULL, VE, Eth-Trunk etc.).
            if parsers.is_physical_iface(row["name"]):
                try:
                    iface_arg = parsers.transceiver_iface_arg(row["name"])
                    tr_text = self.session.run(
                        f"display transceiver interface {iface_arg} verbose",
                        timeout=10.0)
                except Exception:
                    tr_text = ""
                parsers.merge_transceiver_detail(info, tr_text)
            return info

        def done(info: dict):
            self._set_busy(False, "")
            dlg = InterfaceDetailDialog(
                row["name"], info, self,
                history=history_mod.for_session(self.session))
            dlg.exec()

        run_async(fetch, done, self._on_error)

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self._rate_timer.stop()
        self.engine.release_traffic("interfaces-panel")
        super().closeEvent(event)
