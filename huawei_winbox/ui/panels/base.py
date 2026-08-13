"""Base comum dos painéis (Interfaces, BGP, OSPF, ...).

Cada painel é uma janelinha interna estilo WinBox: barra de botões em
cima, tabela embaixo. As ações de configuração passam sempre pela
pré-visualização de CLI antes de irem ao equipamento.
"""

from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer, QItemSelectionModel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QLabel, QMessageBox, QHeaderView, QComboBox,
    QSizePolicy, QLineEdit, QToolButton, QFrame,
)

from ...core.scheduler import BACKGROUND, NORMAL, Dropped
from .. import icons
from ..command_preview import CommandPreviewDialog
from ..flow_layout import FlowLayout
from ..worker import run_async


class BasePanel(QWidget):
    """Painel com tabela, botão Atualizar e infraestrutura de envio.

    As consultas feitas dentro de ``fetch()`` herdam automaticamente a
    prioridade certa: quando o usuário clica em *Atualizar* elas passam à
    frente na fila da sessão; quando vêm do auto-refresh entram como
    trabalho de fundo, podem ser reaproveitadas de outro painel que pediu
    a mesma coisa e são descartadas se envelhecerem esperando. É o que
    permite deixar várias janelas atualizando sozinhas sem travar o app.
    """

    title = "Painel"
    columns: list[str] = []

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._tag = f"{self.__class__.__name__}#{id(self):x}"

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(3)

        # ── Barra de ferramentas estilo WinBox (frame com gradiente) ─── #
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("panelToolbar")
        tf = QVBoxLayout(toolbar_frame)
        tf.setContentsMargins(5, 4, 5, 4)
        tf.setSpacing(3)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("Atualizar")
        self.refresh_btn.setIcon(icons.action_icon("refresh"))
        self.refresh_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.refresh_btn.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_btn)

        self.auto_combo = QComboBox()
        self.auto_combo.addItem("Auto: desligado", 0)
        for secs in (5, 10, 30, 60):
            self.auto_combo.addItem(f"Auto: {secs}s", secs)
        self.auto_combo.currentIndexChanged.connect(self._auto_changed)
        controls.addWidget(self.auto_combo)
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_refresh)

        controls.addStretch(1)
        find_icon = QLabel()
        find_icon.setPixmap(icons.action_icon("find").pixmap(16, 16))
        controls.addWidget(find_icon)
        self.find_edit = QLineEdit()
        self.find_edit.setObjectName("findBox")
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(self._apply_filter)
        controls.addWidget(self.find_edit)
        tf.addLayout(controls)

        # ── Botões específicos do painel (flow: quebram de linha) ─────── #
        self._btn_widget = QWidget()
        self._btn_widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.toolbar = FlowLayout(self._btn_widget, h_spacing=4, v_spacing=3)
        self.build_toolbar(self.toolbar)
        if self.toolbar.count() == 0:
            self._btn_widget.hide()
        else:
            tf.addWidget(self._btn_widget)
        root.addWidget(toolbar_frame)

        # ── Tabela principal ─────────────────────────────────────────── #
        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        # Seleção múltipla: desativar dez interfaces ou dez vizinhos BGP um
        # a um é trabalho repetido e sujeito a erro. Ctrl+clique escolhe
        # avulsos, Shift+clique escolhe faixas, Ctrl+A escolhe tudo o que
        # o filtro Find deixou visível.
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_count_changed)
        root.addWidget(self.table)

        # ── Rodapé: contagem de itens (esq.) | status (dir.) ─────────── #
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.count_label = QLabel("")
        self.count_label.setObjectName("itemCount")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        self.status_label = QLabel("")
        footer.addWidget(self.status_label)
        root.addLayout(footer)

        self.refresh()

    # ----- a serem definidos pelas subclasses ------------------------- #
    def build_toolbar(self, layout: FlowLayout) -> None:
        """Adiciona botões específicos do painel."""

    def fetch(self) -> list[dict]:
        """Roda no worker: consulta o equipamento e retorna as linhas."""
        return []

    def row_values(self, row: dict) -> list[str]:
        """Converte um dicionário do fetch() em células da tabela."""
        return []

    def style_row(self, index: int, row: dict) -> None:
        """Formata a linha recém-criada (cor, negrito).

        Existe para que um painel possa colorir suas linhas sem precisar
        reimplementar ``_on_rows`` inteiro — quem reimplementava acabava
        perdendo silenciosamente tudo o que a base ganhasse depois.
        """

    # ----- infraestrutura --------------------------------------------- #
    @property
    def _auto_seconds(self) -> int:
        return self.auto_combo.currentData() or 0

    def _start_auto_timer(self, secs: int) -> None:
        # Um pouco de desalinhamento entre painéis: sem isto, três janelas
        # em "Auto: 5s" disparam no mesmo instante e formam um pico de
        # consultas a cada 5 segundos.
        self._auto_timer.start(secs * 1000 + random.randint(0, 900))

    def _auto_changed(self):
        secs = self._auto_seconds
        if secs:
            self._start_auto_timer(secs)
            self.refresh()
        else:
            self._auto_timer.stop()

    def hideEvent(self, event):
        self._auto_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        secs = self._auto_seconds
        if secs and not self._auto_timer.isActive():
            self._start_auto_timer(secs)
        super().showEvent(event)

    def _auto_refresh(self):
        self.refresh(background=True)

    def refresh(self, *_signal_args, background: bool = False):
        # ``*_signal_args`` absorve o que os sinais do Qt mandam junto
        # (``clicked(bool)``, ``currentIndexChanged(int)``): sem isso, um
        # índice de combo viraria "atualização de fundo" sem querer.
        if self._busy:
            return
        secs = self._auto_seconds
        if background:
            # Cache um pouco menor que o período: painéis diferentes que
            # rodam o mesmo "display" dividem a leitura em vez de dobrá-la.
            context = {"priority": BACKGROUND,
                       "ttl": max(2.0, secs * 0.7),
                       "max_wait": max(10.0, secs * 3.0),
                       "tag": self._tag}
        else:
            context = {"priority": NORMAL, "ttl": 1.5, "tag": self._tag}
        self._set_busy(True, "Atualizando…" if background
                       else "Consultando equipamento...")

        def guarded():
            try:
                with self.session.request_context(**context):
                    return self.fetch()
            except Dropped:
                # O equipamento estava ocupado; a próxima volta pega o dado.
                return None

        run_async(guarded, self._on_rows,
                  self._on_background_error if background else self._on_error)

    def row_key(self, row: dict) -> str:
        """Identidade estável de uma linha, para reencontrá-la ao atualizar.

        Por padrão é a primeira coluna (nome da interface, IP do vizinho,
        número da VLAN...). Painéis cuja primeira coluna se repete podem
        sobrescrever isto.
        """
        values = self.row_values(row)
        return str(values[0]) if values else ""

    def _on_rows(self, rows: list[dict] | None):
        if rows is None:                     # atualização de fundo descartada
            self._set_busy(False, "")
            return
        # Guarda quem estava selecionado: a tabela é reconstruída do zero a
        # cada atualização e, sem isto, aplicar duas ações no mesmo conjunto
        # (desabilitar e depois habilitar, por exemplo) obrigaria a remarcar
        # tudo de novo — o que dói justamente quando são muitas linhas.
        previous = {self.row_key(row) for row in self.selected_rows()}

        self._rows = rows
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, value in enumerate(self.row_values(row)):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.table.setItem(r, c, item)
            self.style_row(r, row)
        self.table.blockSignals(False)

        if previous:
            self._restore_selection(previous)
        self._apply_filter()
        self._set_busy(False, "")

    def _restore_selection(self, keys: set[str]) -> None:
        selection = self.table.selectionModel()
        if selection is None:
            return
        model = self.table.model()
        flags = (QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            row = self.table.item(r, 0).data(Qt.UserRole) \
                if self.table.item(r, 0) else None
            if row is not None and self.row_key(row) in keys:
                selection.select(model.index(r, 0), flags)
        self.table.blockSignals(False)

    # ----- filtro "Find" + contagem de itens (estilo WinBox) ---------- #
    def _apply_filter(self):
        text = self.find_edit.text().lower().strip()
        total = self.table.rowCount()
        shown = 0
        for r in range(total):
            match = not text
            if text:
                for c in range(self.table.columnCount()):
                    cell = self.table.item(r, c)
                    if cell and text in cell.text().lower():
                        match = True
                        break
            self.table.setRowHidden(r, not match)
            shown += int(match)
        self._update_count(shown, total)

    def _update_count(self, shown: int, total: int):
        if total == 0:
            text = "0 itens"
        elif shown == total:
            text = f"{total} {'item' if total == 1 else 'itens'}"
        else:
            text = f"{shown} de {total} itens"
        # Quantos estão marcados importa tanto quanto quantos existem: é o
        # número que diz sobre o que a próxima ação vai agir.
        selected = len(self.selected_rows())
        if selected > 1:
            text += f"  ·  {selected} selecionados"
        self.count_label.setText(text)

    def _selection_count_changed(self):
        self._update_count(
            sum(1 for r in range(self.table.rowCount())
                if not self.table.isRowHidden(r)),
            self.table.rowCount())

    def _on_error(self, message: str):
        self._set_busy(False, "")
        if not self.isVisible():
            # A janela (ou a sessão) já foi fechada enquanto a consulta
            # estava em voo — não faz sentido abrir um alerta agora.
            return
        QMessageBox.warning(self, self.title, message)

    def _on_background_error(self, message: str):
        """Falha numa atualização automática: avisa no rodapé, sem modal.

        Abrir uma caixa de diálogo a cada tropeço do auto-refresh deixaria
        a tela inutilizável justamente quando o equipamento está sob carga.
        """
        self._set_busy(False, f"⚠ {message.splitlines()[0][:120]}")

    def _set_busy(self, busy: bool, text: str):
        self._busy = busy
        self.refresh_btn.setEnabled(not busy)
        self.status_label.setText(text)

    # ----- seleção ---------------------------------------------------- #
    def selected_rows(self) -> list[dict]:
        """Linhas selecionadas, na ordem em que aparecem na tabela.

        ``selectedItems()`` devolve *células*; como a seleção é por linha,
        a mesma linha volta uma vez por coluna. Aqui elas são reduzidas a
        um registro por linha, ignorando as ocultas pelo filtro.
        """
        found: dict[int, dict] = {}
        for item in self.table.selectedItems():
            row = item.row()
            if row in found or self.table.isRowHidden(row):
                continue
            data = item.data(Qt.UserRole)
            if data is not None:
                found[row] = data
        return [found[index] for index in sorted(found)]

    def selected_row(self) -> dict | None:
        """Primeira linha selecionada (para ações que só fazem sentido em uma)."""
        rows = self.selected_rows()
        return rows[0] if rows else None

    def require_selection(self, what: str = "um item") -> list[dict]:
        """Linhas selecionadas; avisa e devolve lista vazia se não houver."""
        rows = self.selected_rows()
        if not rows:
            QMessageBox.information(
                self, self.title,
                f"Selecione {what} na tabela.\n\n"
                f"Para escolher vários: Ctrl+clique (avulsos), "
                f"Shift+clique (faixa) ou Ctrl+A (todos).")
        return rows

    def apply_for_each(self, rows: list[dict], builder,
                       refresh_after: bool = True,
                       system_view: bool = True) -> None:
        """Monta os comandos de cada linha e envia tudo num bloco só.

        Um único envio para N itens: a pré-visualização mostra a lista
        inteira antes de sair, e o equipamento recebe um bloco de
        configuração em vez de N idas e voltas.
        """
        commands: list[str] = []
        for row in rows:
            commands.extend(builder(row) or [])
        if not commands:
            return
        self.apply_commands(commands, refresh_after=refresh_after,
                            system_view=system_view)

    def closeEvent(self, event):
        """Fechar a janela tira da fila o que ela ainda tinha pendente."""
        self._auto_timer.stop()
        try:
            self.session.scheduler.cancel(self._tag)
        except Exception:                    # noqa: BLE001 — só limpeza
            pass
        super().closeEvent(event)

    def apply_commands(self, commands: list[str],
                       refresh_after: bool = True,
                       system_view: bool = True):
        """Mostra a pré-visualização e, se confirmado, envia via SSH."""
        two_stage = system_view and getattr(self.session, "two_stage", False)
        if not CommandPreviewDialog.confirm(commands, self,
                                            two_stage=two_stage,
                                            system_view=system_view):
            return
        self._set_busy(True, "Enviando configuração...")

        def send():
            if system_view:
                return self.session.configure(commands, tag=self._tag)
            return self.session.run_many(commands, tag=self._tag)

        def done(output: str):
            self._set_busy(False, "Configuração aplicada")
            if "Error" in output or "error" in output:
                QMessageBox.warning(
                    self, self.title,
                    "O equipamento reportou um problema:\n\n" + output)
            if refresh_after:
                self.refresh()

        run_async(send, done, self._on_error)
