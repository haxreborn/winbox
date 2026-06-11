"""Painel de Interfaces — equivalente à janela *Interfaces* do WinBox.

Usa ``display interface description`` para mostrar os nomes reais
configurados nas portas. Duplo clique abre os detalhes completos da
porta (velocidade, duplex, negociação, potência ótica, contadores).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPushButton, QMessageBox, QDialog, QVBoxLayout, QGridLayout,
    QLabel, QPlainTextEdit, QDialogButtonBox, QGroupBox,
)

from ...core import parsers, translator
from ...core.session import _LONG_TIMEOUT
from ..forms import SimpleForm
from ..worker import run_async
from .base import BasePanel


class InterfaceDetailDialog(QDialog):
    """Diálogo com detalhes completos da porta (double-click)."""

    def __init__(self, iface_name: str, info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Detalhes — {iface_name}")
        self.resize(600, 520)
        layout = QVBoxLayout(self)

        def grp(title: str, pairs: list[tuple[str, str]]) -> QGroupBox:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            for row, (label, value) in enumerate(pairs):
                lbl = QLabel(f"<b>{label}:</b>")
                val = QLabel(value or "—")
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(lbl, row, 0)
                grid.addWidget(val, row, 1)
            return box

        estado_cor = "#2e8b57" if "UP" in info["state"].upper() else "#c0392b"
        status_lbl = QLabel(
            f'<b>Estado:</b>  '
            f'<span style="color:{estado_cor}; font-weight:bold">'
            f'{info["state"] or "—"}</span>'
            f'  &nbsp;&nbsp;  <b>Protocolo:</b>  '
            f'<span style="color:{estado_cor}; font-weight:bold">'
            f'{info["protocol"] or "—"}</span>'
        )
        layout.addWidget(status_lbl)

        if info["description"]:
            desc = QLabel(f'<b>Descrição:</b>  {info["description"]}')
            layout.addWidget(desc)

        # Camada física
        phys_pairs = [
            ("Velocidade", info["speed"]),
            ("Duplex", info["duplex"]),
            ("Negociação", info["negotiation"]),
            ("MTU", info["mtu"]),
        ]
        layout.addWidget(grp("Camada física", phys_pairs))

        # Módulo óptico (SFP) — só mostra se tiver algum dado
        optical_pairs = [
            ("Tipo de transceptor", info["transceiver_type"]),
            ("TX Power", info["tx_power"]),
            ("RX Power", info["rx_power"]),
            ("Corrente do laser", info["bias_current"]),
            ("Temperatura do módulo", info["temperature"]),
        ]
        if any(v for _, v in optical_pairs):
            layout.addWidget(grp("Módulo óptico (SFP/SFP+)", optical_pairs))

        # Contadores
        counter_pairs = [
            ("Utilização de entrada", info["input_rate"]),
            ("Utilização de saída", info["output_rate"]),
            ("Erros de entrada", info["input_errors"]),
            ("Erros de saída", info["output_errors"]),
        ]
        layout.addWidget(grp("Contadores", counter_pairs))

        # Saída bruta do equipamento
        raw_box = QGroupBox("Saída completa do equipamento")
        raw_layout = QVBoxLayout(raw_box)
        raw_edit = QPlainTextEdit(info["raw"])
        raw_edit.setObjectName("terminal")
        raw_edit.setReadOnly(True)
        raw_edit.setMaximumHeight(140)
        raw_layout.addWidget(raw_edit)
        layout.addWidget(raw_box)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


class InterfacesPanel(BasePanel):
    title = "Interfaces"
    columns = ["Interface", "Físico", "Protocolo", "Descrição"]

    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        # Conecta uma única vez — não em _on_rows, que roda a cada refresh.
        self.table.itemDoubleClicked.connect(self._show_detail_from_item)

    def build_toolbar(self, layout):
        for text, slot in [
            ("Habilitar", lambda: self._toggle(True)),
            ("Desabilitar", lambda: self._toggle(False)),
            ("Descrição...", self._edit_description),
            ("Endereço IP...", self._add_ip),
            ("MTU...", self._set_mtu),
            ("Detalhes", self._show_detail),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def fetch(self):
        return parsers.parse_interface_description(
            self.session.run("display interface description"))

    def row_values(self, row):
        return [row["name"], row["phy"], row["protocol"], row["description"]]

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

    def _show_detail(self):
        row = self._need_selection()
        if row:
            self._open_detail(row)

    def _open_detail(self, row: dict):
        self._set_busy(True, f"Consultando {row['name']}...")

        def fetch():
            text = self.session.run(
                f"display interface {row['name']}", timeout=_LONG_TIMEOUT)
            return parsers.parse_interface_detail(text)

        def done(info: dict):
            self._set_busy(False, "")
            dlg = InterfaceDetailDialog(row["name"], info, self)
            dlg.exec()

        run_async(fetch, done, self._on_error)
