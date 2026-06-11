"""Terminal interativo — conexão SSH dedicada, tudo na área preta.

Toda a interação acontece no widget de saída (fundo preto), como no PuTTY:
o usuário vê o prompt do equipamento e digita diretamente nele. Cada tecla
é enviada ao dispositivo imediatamente; o próprio VRP faz o eco de volta.
O QTimer de 40 ms exibe tudo que chegar do equipamento em streaming.

A conexão é aberta em background (nova SSHClient dedicada) — a janela nunca
congela, mesmo que o equipamento demore para aceitar a conexão.
"""

from __future__ import annotations

import re
import socket

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit

from ..worker import run_async

# Remove sequências ANSI problemáticas. Backspaces (\x08) NÃO são
# removidos aqui: são aplicados um a um em _append (cada \x08 apaga o
# caractere anterior na tela), porque o eco "\x08 \x08" do equipamento
# pode chegar dividido entre dois recv() — regex sobre o chunk não trata.
# Assim "---- More ----" e a linha digitada também são apagados
# naturalmente quando o VRP os apaga com BS+espaço+BS.
# O padrão \x1b[ND ... \x1b[ND (apaga linha via cursor) precisa vir ANTES
# do CSI genérico, senão o CSI consome o \x1b[ND e deixa os espaços.
_STRIP = re.compile(
    r"\x1b\[\d+D\s*\x1b\[\d+D"         # VRP: apaga linha (left+spaces+left)
    r"|\x1b\[[0-9;]*[A-Za-z]"          # CSI sequences (cores, cursor)
    r"|\x1b\][^\x07]*\x07"             # OSC sequences (título)
    r"|\x1b[>=]"                        # DECPNM / DECPAM
)


class _TerminalView(QPlainTextEdit):
    """Widget de saída que também captura teclas e as envia ao canal SSH.

    Não usa QLineEdit separado — tudo acontece na mesma tela preta, como
    o PuTTY. Caracteres digitados são enviados ao dispositivo; o eco de
    volta aparece via _poll() no terminal.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminal")
        self.setReadOnly(True)   # apenas o canal SSH "escreve" aqui
        self.setMaximumBlockCount(5000)
        self._chan = None
        self._history: list[str] = []
        self._hist_pos = 0
        self._typed: str = ""    # buffer local do que o usuário está digitando

    def attach(self, chan) -> None:
        self._chan = chan

    def detach(self) -> None:
        self._chan = None

    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event) -> None:  # noqa: C901 — switch é extenso
        chan = self._chan
        key = event.key()
        mods = event.modifiers()
        char = event.text()

        # Scroll sem canal (visualização do histórico da sessão)
        if key == Qt.Key.Key_PageUp:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - self.height())
            return
        if key == Qt.Key.Key_PageDown:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + self.height())
            return

        if not chan or chan.closed:
            return

        # ── Enter ───────────────────────────────────────────────────────
        # Cada caractere já foi enviado ao equipamento quando foi digitado;
        # aqui vai SOMENTE o \n (reenviar o comando inteiro duplicaria tudo).
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cmd = self._typed
            if cmd.strip():
                self._history.append(cmd)
            self._hist_pos = len(self._history)
            self._typed = ""
            self._send("\n")
            return

        # ── Backspace ───────────────────────────────────────────────────
        # Envia só o \x08; o equipamento ecoa "\x08 \x08" e o _append
        # aplica cada \x08 apagando o caractere na tela (BS apaga o char
        # digitado, o espaço entra e o BS seguinte apaga o espaço). Isso
        # funciona para qualquer quantidade de backspaces seguidos, mesmo
        # com o eco chegando dividido entre dois recv().
        if key == Qt.Key.Key_Backspace:
            if self._typed:
                self._typed = self._typed[:-1]
                self._send("\x08")
            return

        # ── ↑ histórico ─────────────────────────────────────────────────
        if key == Qt.Key.Key_Up:
            if self._history and self._hist_pos > 0:
                self._hist_pos -= 1
                self._replace_typed(self._history[self._hist_pos])
            return

        # ── ↓ histórico ─────────────────────────────────────────────────
        if key == Qt.Key.Key_Down:
            if self._hist_pos < len(self._history) - 1:
                self._hist_pos += 1
                self._replace_typed(self._history[self._hist_pos])
            else:
                self._hist_pos = len(self._history)
                self._replace_typed("")
            return

        # ── Tab: autocomplete do VRP ────────────────────────────────────
        # O texto parcial já está na linha do equipamento; envia só o \t.
        if key == Qt.Key.Key_Tab:
            self._send("\t")
            return

        # ── Ctrl+C: interrompe comando ──────────────────────────────────
        if key == Qt.Key.Key_C and mods == Qt.KeyboardModifier.ControlModifier:
            self._typed = ""
            self._send("\x03")
            return

        # ── Ctrl+Z ──────────────────────────────────────────────────────
        if key == Qt.Key.Key_Z and mods == Qt.KeyboardModifier.ControlModifier:
            self._send("\x1a")
            return

        # ── Caractere imprimível ────────────────────────────────────────
        if char:
            self._typed += char
            self._send(char)

    # ------------------------------------------------------------------ #
    def _send(self, data: str | bytes) -> None:
        chan = self._chan
        if not chan or chan.closed:
            return
        try:
            chan.send(data)
        except Exception:
            pass

    def _replace_typed(self, new_text: str) -> None:
        """Substitui o texto digitado: Ctrl+U limpa a linha, depois re-digita."""
        self._send("\x15")          # Ctrl+U — limpa linha atual no VRP VTY
        self._typed = new_text
        if new_text:
            self._send(new_text)


class TerminalPanel(QWidget):
    title = "Terminal"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._client = None
        self._chan = None
        self._closing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        self.output = _TerminalView(self)
        root.addWidget(self.output)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(40)

        self._connect_async()
        self.output.setFocus()

    # ------------------------------------------------------------------ #
    # Conexão
    # ------------------------------------------------------------------ #

    def _connect_async(self) -> None:
        self._append(
            f"Conectando a {self.session.host} (nova sessão SSH dedicada)...\n")

        def done(result):
            client, chan = result
            if self._closing:
                try:
                    chan.close()
                    client.close()
                except Exception:
                    pass
                return
            self._client, self._chan = client, chan
            self.output.attach(chan)
            # Desativa paginação nesta sessão também.
            try:
                chan.send("screen-length 0 temporary\n")
            except Exception:
                pass

        def fail(msg: str):
            self._append(
                f"\n[Falha ao conectar: {msg}]\n"
                "Feche e reabra esta janela para tentar novamente.\n")

        run_async(self.session.open_terminal, done, fail)

    def closeEvent(self, event) -> None:
        self._closing = True
        self._timer.stop()
        self.output.detach()
        for obj in (self._chan, self._client):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    def _poll(self) -> None:
        chan = self._chan
        if not chan or chan.closed:
            return
        try:
            data = chan.recv(4096)
        except socket.timeout:
            return
        except Exception:
            return
        if not data:
            return
        text = (data.decode("utf-8", errors="replace")
                .replace("\r\n", "\n").replace("\r", "\n"))
        text = _STRIP.sub("", text)
        if text:
            self._append(text)

    def _append(self, text: str) -> None:
        """Insere texto no fim, aplicando cada \\x08 como "apaga anterior".

        O eco de apagar do VRP é "\\x08 \\x08" (volta, espaço, volta): o
        primeiro BS apaga o caractere digitado, o espaço entra na tela e o
        segundo BS apaga o espaço — resultado visual idêntico ao PuTTY,
        mesmo quando a sequência chega dividida entre dois recv().
        """
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        parts = text.split("\x08")
        cursor.insertText(parts[0])
        for part in parts[1:]:
            cursor.deletePreviousChar()
            cursor.insertText(part)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
