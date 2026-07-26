"""Painel Configuração — captura, navega, compara e envia o config do VRP.

É o painel que atende diretamente ao propósito do aplicativo: **pegar
tudo o que está configurado no equipamento** e deixar isso visual.

O que dá para fazer aqui:

* baixar a configuração em execução (``display current-configuration``)
  e navegar por ela em blocos (interfaces, BGP, ACLs, AAA, ...) em vez de
  rolar milhares de linhas;
* procurar texto com destaque, em qualquer bloco;
* **comparar** a configuração em execução com a gravada na flash — é o
  jeito direto de ver o que ainda não foi salvo (e se perderia num
  reboot);
* tirar um *instantâneo* e comparar depois, para ver exatamente o que
  mudou enquanto você trabalhava;
* salvar tudo em arquivo (backup) e enviar um bloco de comandos colado,
  sempre com a pré-visualização do CLI antes de aplicar.
"""

from __future__ import annotations

import difflib
import re
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QPlainTextEdit, QSplitter, QListWidget, QListWidgetItem,
    QMessageBox, QFileDialog, QDialog, QDialogButtonBox, QComboBox, QTextEdit,
    QInputDialog,
)

from ...core import backup
from ...core.scheduler import BACKGROUND, NORMAL
from ..command_preview import CommandPreviewDialog
from ..worker import run_async

# Blocos do VRP são separados por linhas contendo apenas "#".
_SEPARATOR = re.compile(r"^\s*#\s*$")
# Linhas de ruído que mudam sozinhas e poluiriam qualquer comparação.
_VOLATILE = re.compile(
    r"^\s*(?:!.*|#.*)?$"
    r"|^\s*(?:clock|display|sysname\s+\S+\s+time)\b"
    r"|current\s+time|Time\s*:\s*\d",
    re.IGNORECASE)

_ADD_COLOR = QColor("#e6f4ea")
_DEL_COLOR = QColor("#fdecea")
_HIT_COLOR = QColor("#ffe9a8")


def split_sections(text: str) -> list[tuple[str, str]]:
    """Divide a configuração em blocos ``(título, corpo)``.

    O VRP separa blocos com uma linha ``#``; o título é a primeira linha
    útil do bloco (``interface GigabitEthernet0/0/1``, ``bgp 65000``...).
    """
    sections: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip("\n")
        if not body.strip():
            return
        title = next((ln.strip() for ln in body.splitlines() if ln.strip()),
                     "(bloco)")
        sections.append((title, body))

    for line in text.splitlines():
        if _SEPARATOR.match(line):
            flush()
            buffer = []
            continue
        buffer.append(line)
    flush()
    return sections


def normalize_for_diff(text: str) -> list[str]:
    """Remove linhas voláteis para a comparação não acusar ruído."""
    return [line.rstrip() for line in text.splitlines()
            if not _VOLATILE.match(line)]


class SendBlockDialog(QDialog):
    """Cola um bloco de CLI para envio (com pré-visualização depois)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enviar bloco de configuração")
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Cole os comandos, um por linha. O aplicativo entra em "
            "<b>system-view</b> sozinho (e envia <b>commit</b> nos "
            "equipamentos que exigem), então não inclua essas linhas.\n"
            "Você ainda verá a pré-visualização antes de qualquer envio."))
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("terminal")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.editor.setFont(font)
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def commands(self) -> list[str]:
        out = []
        for line in self.editor.toPlainText().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!")):
                continue
            if stripped.lower() in ("system-view", "sys", "return", "quit",
                                    "commit"):
                continue
            out.append(stripped)
        return out


class BackupHistoryDialog(QDialog):
    """Histórico de cópias em disco: abrir uma, ou comparar duas."""

    def __init__(self, host: str, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Histórico de configurações — {host}")
        self.resize(760, 480)
        self._host = host
        self._current = current
        self.result_text = ""
        self.result_title = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Selecione <b>uma</b> cópia para abrir, ou <b>duas</b> para ver "
            "o que mudou entre elas. Com uma selecionada, "
            "<b>Comparar com a atual</b> mostra o que mudou até agora."))

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.open_btn = QPushButton("Abrir")
        self.open_btn.clicked.connect(lambda: self._finish("open"))
        row.addWidget(self.open_btn)
        self.diff_btn = QPushButton("Comparar as duas")
        self.diff_btn.clicked.connect(lambda: self._finish("diff"))
        row.addWidget(self.diff_btn)
        self.vs_now_btn = QPushButton("Comparar com a atual")
        self.vs_now_btn.clicked.connect(lambda: self._finish("now"))
        row.addWidget(self.vs_now_btn)
        row.addStretch(1)
        self.folder_label = QLabel("")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.folder_label)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Fechar")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        entries = backup.list_backups(self._host)
        for entry in entries:
            when = time.strftime("%d/%m/%Y  %H:%M:%S",
                                 time.localtime(entry["when"]))
            size = f"{entry['size'] / 1024:.1f} kB"
            label = f"  —  {entry['label']}" if entry["label"] else ""
            item = QListWidgetItem(f"{when}      {size:>9}{label}")
            item.setData(Qt.UserRole, entry)
            self.list.addItem(item)
        self.folder_label.setText(
            f"{len(entries)} cópia(s) em {backup.directory_for(self._host)}")
        self._selection_changed()

    def _selected(self) -> list[dict]:
        return [item.data(Qt.UserRole) for item in self.list.selectedItems()]

    def _selection_changed(self) -> None:
        count = len(self._selected())
        self.open_btn.setEnabled(count == 1)
        self.vs_now_btn.setEnabled(count == 1)
        self.diff_btn.setEnabled(count == 2)

    def _finish(self, mode: str) -> None:
        chosen = sorted(self._selected(), key=lambda e: e["when"])
        if mode == "open" and len(chosen) == 1:
            self.result_text = backup.read(chosen[0]["path"])
            self.result_title = _stamp_label(chosen[0])
        elif mode == "diff" and len(chosen) == 2:
            self.result_text = None       # sinaliza comparação ao chamador
            self.result_title = (_stamp_label(chosen[0]),
                                 _stamp_label(chosen[1]),
                                 backup.read(chosen[0]["path"]),
                                 backup.read(chosen[1]["path"]))
        elif mode == "now" and len(chosen) == 1:
            self.result_text = None
            self.result_title = (_stamp_label(chosen[0]), "em execução agora",
                                 backup.read(chosen[0]["path"]),
                                 self._current)
        else:
            return
        self.accept()


def _stamp_label(entry: dict) -> str:
    # Com segundos: duas cópias do mesmo minuto ficavam com rótulos
    # idênticos, e a comparação não dizia qual era qual.
    when = time.strftime("%d/%m %H:%M:%S", time.localtime(entry["when"]))
    return f"{when}{' — ' + entry['label'] if entry['label'] else ''}"


class ConfigPanel(QWidget):
    title = "Configuração"
    state_key = "config"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._busy = False
        self._current = ""
        self._saved = ""
        self._snapshot = ""
        self._snapshot_at = 0.0
        self._sections: list[tuple[str, str]] = []
        self._watch_at = 0.0
        self._auto_backup_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        self.section_list = QListWidget()
        self.section_list.setMinimumWidth(230)
        self.section_list.currentRowChanged.connect(self._show_section)
        splitter.addWidget(self.section_list)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setObjectName("terminal")
        self.viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.viewer.setFont(mono)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 700])
        root.addWidget(splitter, 1)

        self.status = QLabel("Baixando configuração…")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        # Vigia de alterações: compara periodicamente com o instantâneo.
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_tick)

        self.refresh()

    # ================================================================== #
    def _build_toolbar(self) -> QVBoxLayout:
        outer = QVBoxLayout()
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(4)
        for text, slot, tip in [
            ("Atualizar", self.refresh, "Baixa a configuração em execução."),
            ("Comparar com a salva", self._diff_saved,
             "Mostra o que está em execução mas ainda NÃO foi gravado na "
             "flash — é o que se perderia num reboot."),
            ("Marcar instantâneo", self._take_snapshot,
             "Guarda o estado atual para comparar depois."),
            ("Comparar com instantâneo", lambda: self._diff_snapshot(False),
             "Mostra o que mudou desde o instantâneo marcado."),
            ("Backup agora", self._backup_now,
             "Guarda uma cópia datada em ~/.huaweibox/backups. Cópias "
             "idênticas à anterior não geram arquivo novo."),
            ("Histórico…", self._show_history,
             "Abre cópias antigas e compara duas datas — é como se descobre "
             "o que mudou entre ontem e hoje."),
            ("Salvar em arquivo…", self._save_file,
             "Exporta a configuração para onde você escolher."),
            ("Enviar bloco…", self._send_block,
             "Cola comandos e envia (com pré-visualização)."),
        ]:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        outer.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        row2.addWidget(QLabel("Procurar:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(
            "texto no bloco exibido (Enter vai para a próxima ocorrência)")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(self._highlight)
        self.find_edit.returnPressed.connect(self._find_next)
        row2.addWidget(self.find_edit, 1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar blocos…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMaximumWidth(220)
        self.filter_edit.textChanged.connect(self._filter_sections)
        row2.addWidget(self.filter_edit)

        row2.addWidget(QLabel("Vigiar mudanças:"))
        self.watch_combo = QComboBox()
        self.watch_combo.addItem("desligado", 0)
        for secs in (30, 60, 300):
            self.watch_combo.addItem(f"a cada {secs}s", secs)
        self.watch_combo.setToolTip(
            "Compara a configuração periodicamente com o instantâneo e "
            "avisa quando algo mudar — inclusive alterações feitas por "
            "outra pessoa, em outra sessão.")
        self.watch_combo.currentIndexChanged.connect(self._watch_changed)
        row2.addWidget(self.watch_combo)
        outer.addLayout(row2)
        return outer

    # ================================================================== #
    # Coleta
    # ================================================================== #
    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.setText("Baixando configuração em execução…")
        session = self.session

        def fetch() -> str:
            return session.run("display current-configuration", timeout=120.0,
                               priority=NORMAL, ttl=5.0, tag="config")

        def done(text: str) -> None:
            self._busy = False
            self._current = text
            self._sections = split_sections(text)
            self._fill_sections()
            lines = len(text.splitlines())
            note = ""
            # Backup automático na primeira leitura da sessão: se nada mudou
            # desde a última cópia, nenhum arquivo é criado.
            if not self._auto_backup_done:
                self._auto_backup_done = True
                entry = backup.save(self.session.host, text, "automático")
                if entry is not None:
                    note = "  ·  cópia guardada no histórico"
            self.status.setText(
                f"Configuração em execução — {lines} linhas em "
                f"{len(self._sections)} blocos · baixada às "
                f"{time.strftime('%H:%M:%S')}{note}")

        def fail(message: str) -> None:
            self._busy = False
            self.status.setText(f"Falha ao baixar a configuração: {message}")
            QMessageBox.warning(self, self.title, message)

        run_async(fetch, done, fail)

    def _fill_sections(self) -> None:
        self.section_list.blockSignals(True)
        self.section_list.clear()
        item = QListWidgetItem("⟨ configuração completa ⟩")
        item.setData(Qt.UserRole, -1)
        self.section_list.addItem(item)
        for index, (title, body) in enumerate(self._sections):
            count = len(body.splitlines())
            entry = QListWidgetItem(
                f"{title}    ({count} {'linha' if count == 1 else 'linhas'})")
            entry.setData(Qt.UserRole, index)
            entry.setToolTip(title)
            self.section_list.addItem(entry)
        self.section_list.blockSignals(False)
        self.section_list.setCurrentRow(0)
        self._filter_sections()

    def _filter_sections(self) -> None:
        text = self.filter_edit.text().strip().lower()
        for row in range(self.section_list.count()):
            item = self.section_list.item(row)
            index = item.data(Qt.UserRole)
            if index is None or index < 0:
                item.setHidden(False)
                continue
            title, body = self._sections[index]
            hit = (not text) or text in title.lower() or text in body.lower()
            item.setHidden(not hit)

    def _show_section(self, row: int) -> None:
        if row < 0:
            return
        item = self.section_list.item(row)
        if item is None:
            return
        index = item.data(Qt.UserRole)
        if index is None or index < 0:
            self.viewer.setPlainText(self._current)
        else:
            self.viewer.setPlainText(self._sections[index][1])
        self._highlight()

    # ================================================================== #
    # Busca
    # ================================================================== #
    def _highlight(self) -> None:
        text = self.find_edit.text()
        selections = []
        if text:
            fmt = QTextCharFormat()
            fmt.setBackground(_HIT_COLOR)
            document = self.viewer.document()
            cursor = QTextCursor(document)
            hits = 0
            while True:
                cursor = document.find(text, cursor)
                if cursor.isNull():
                    break
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format = fmt
                selections.append(selection)
                hits += 1
                if hits > 2000:
                    break
        self.viewer.setExtraSelections(selections)

    def _find_next(self) -> None:
        text = self.find_edit.text()
        if not text:
            return
        if not self.viewer.find(text):
            cursor = self.viewer.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.viewer.setTextCursor(cursor)
            self.viewer.find(text)

    # ================================================================== #
    # Comparações
    # ================================================================== #
    def _render_diff(self, left: str, right: str, left_label: str,
                     right_label: str, empty_message: str) -> int:
        diff = list(difflib.unified_diff(
            normalize_for_diff(left), normalize_for_diff(right),
            fromfile=left_label, tofile=right_label, lineterm="", n=2))
        self.section_list.setCurrentRow(0)
        if not diff:
            self.viewer.setPlainText(empty_message)
            return 0
        self.viewer.setPlainText("\n".join(diff))
        # Colore as linhas adicionadas/removidas.
        selections = []
        document = self.viewer.document()
        for number in range(document.blockCount()):
            block = document.findBlockByNumber(number)
            line = block.text()
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                color = _ADD_COLOR
            elif line.startswith("-"):
                color = _DEL_COLOR
            else:
                continue
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            fmt.setProperty(QTextCharFormat.FullWidthSelection, True)
            cursor = QTextCursor(block)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)
        return sum(1 for line in diff
                   if line[:1] in "+-" and not line.startswith(("+++", "---")))

    def _diff_saved(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.setText("Baixando a configuração salva na flash…")
        session = self.session

        def fetch() -> tuple[str, str]:
            current = session.run("display current-configuration",
                                  timeout=120.0, priority=NORMAL, ttl=5.0,
                                  tag="config")
            saved = session.run("display saved-configuration", timeout=120.0,
                                priority=NORMAL, ttl=30.0, tag="config")
            return current, saved

        def done(data: tuple[str, str]) -> None:
            self._busy = False
            self._current, self._saved = data
            self._sections = split_sections(self._current)
            changes = self._render_diff(
                self._saved, self._current, "salva na flash", "em execução",
                "A configuração em execução é idêntica à gravada na flash.\n"
                "Não há nada pendente de 'save'.")
            if changes:
                self.status.setText(
                    f"⚠ {changes} linha(s) diferem da configuração salva — "
                    f"use Sessão → Salvar configuração para gravar na flash. "
                    f"Verde = existe agora; vermelho = existe só na flash.")
            else:
                self.status.setText(
                    "Tudo gravado: nada se perderia num reboot.")

        def fail(message: str) -> None:
            self._busy = False
            self.status.setText(f"Falha na comparação: {message}")
            QMessageBox.warning(self, self.title, message)

        run_async(fetch, done, fail)

    def _take_snapshot(self) -> None:
        if not self._current:
            QMessageBox.information(
                self, self.title,
                "Atualize a configuração antes de marcar o instantâneo.")
            return
        self._snapshot = self._current
        self._snapshot_at = time.time()
        self.status.setText(
            "Instantâneo marcado às "
            f"{time.strftime('%H:%M:%S', time.localtime(self._snapshot_at))} — "
            "use 'Comparar com instantâneo' para ver o que mudou desde então.")

    def _diff_snapshot(self, silent: bool = False) -> None:
        if not self._snapshot:
            if not silent:
                QMessageBox.information(
                    self, self.title,
                    "Marque um instantâneo primeiro.")
            return
        if self._busy:
            return
        self._busy = True
        session = self.session
        marked = time.strftime("%H:%M:%S", time.localtime(self._snapshot_at))

        def fetch() -> str:
            return session.run("display current-configuration", timeout=120.0,
                               priority=BACKGROUND if silent else NORMAL,
                               ttl=5.0, max_wait=90.0 if silent else None,
                               tag="config")

        def done(text: str) -> None:
            self._busy = False
            self._current = text
            self._sections = split_sections(text)
            changed = normalize_for_diff(text) != normalize_for_diff(
                self._snapshot)
            if silent and not changed:
                self.status.setText(
                    f"Vigiando: nada mudou desde {marked} · última checagem "
                    f"{time.strftime('%H:%M:%S')}")
                return
            changes = self._render_diff(
                self._snapshot, text, f"instantâneo {marked}", "agora",
                f"Nada mudou desde o instantâneo de {marked}.")
            if changes:
                self.status.setText(
                    f"{changes} linha(s) mudaram desde {marked}. "
                    f"Verde = adicionado; vermelho = removido.")
            else:
                self.status.setText(f"Nada mudou desde {marked}.")

        def fail(message: str) -> None:
            self._busy = False
            if not silent:
                QMessageBox.warning(self, self.title, message)

        run_async(fetch, done, fail)

    def _watch_changed(self) -> None:
        seconds = self.watch_combo.currentData() or 0
        if not seconds:
            self._watch_timer.stop()
            return
        if not self._snapshot:
            self._take_snapshot()
        self._watch_timer.start(seconds * 1000)

    def _watch_tick(self) -> None:
        if self.isVisible():
            self._diff_snapshot(silent=True)

    # ================================================================== #
    # Arquivo / envio
    # ================================================================== #
    def _backup_now(self) -> None:
        if not self._current.strip():
            QMessageBox.information(
                self, self.title,
                "Baixe a configuração antes de guardar uma cópia.")
            return
        label, ok = QInputDialog.getText(
            self, "Backup da configuração",
            "Rótulo para esta cópia (opcional — ex.: 'antes do BGP'):")
        if not ok:
            return
        entry = backup.save(self.session.host, self._current, label.strip())
        if entry is None:
            self.status.setText(
                "Nada mudou desde a última cópia — nenhum arquivo novo "
                "foi criado.")
            return
        self.status.setText(f"Cópia guardada em {entry['path']}")

    def _show_history(self) -> None:
        dialog = BackupHistoryDialog(self.session.host, self._current, self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.result_text:
            # Abrir uma cópia: mostra o conteúdo dela, em blocos.
            self._sections = split_sections(dialog.result_text)
            self._fill_sections()
            self.viewer.setPlainText(dialog.result_text)
            self.status.setText(
                f"Mostrando a cópia de {dialog.result_title} — "
                f"'Atualizar' volta para a configuração em execução.")
            return
        left_label, right_label, left, right = dialog.result_title
        changes = self._render_diff(
            left, right, left_label, right_label,
            f"Nada mudou entre {left_label} e {right_label}.")
        if changes:
            self.status.setText(
                f"{changes} linha(s) diferem entre {left_label} e "
                f"{right_label}. Verde = existe em '{right_label}'; "
                f"vermelho = só em '{left_label}'.")
        else:
            self.status.setText(
                f"Nada mudou entre {left_label} e {right_label}.")

    def _save_file(self) -> None:
        if not self._current:
            QMessageBox.information(self, self.title,
                                    "Não há configuração baixada ainda.")
            return
        stamp = time.strftime("%Y%m%d-%H%M")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Salvar configuração",
            f"{self.session.hostname}-{stamp}.cfg",
            "Configuração (*.cfg);;Texto (*.txt);;Todos (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._current)
        except OSError as exc:
            QMessageBox.warning(self, self.title, f"Falha ao gravar: {exc}")
            return
        self.status.setText(f"Configuração salva em {path}")

    def _send_block(self) -> None:
        dialog = SendBlockDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        commands = dialog.commands()
        if not commands:
            return
        two_stage = getattr(self.session, "two_stage", False)
        if not CommandPreviewDialog.confirm(commands, self,
                                            two_stage=two_stage,
                                            system_view=True):
            return
        self.status.setText("Enviando configuração…")
        session = self.session

        def send() -> str:
            return session.configure(commands, tag="config")

        def done(output: str) -> None:
            if re.search(r"\bError\b", output):
                QMessageBox.warning(
                    self, self.title,
                    "O equipamento reportou um problema:\n\n" + output)
            self.status.setText("Configuração enviada.")
            self.refresh()

        def fail(message: str) -> None:
            self.status.setText("Falha no envio.")
            QMessageBox.warning(self, self.title, message)

        run_async(send, done, fail)

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        self._watch_timer.stop()
        self.session.scheduler.cancel("config")
        super().closeEvent(event)
