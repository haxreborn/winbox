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
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit

from ..worker import run_async

# Remove sequências ANSI e todos os padrões de apagar caractere que o
# VRP envia como eco do backspace. O delete é feito LOCALMENTE no
# keyPressEvent (imediato, sem esperar o eco) — o eco chega depois e
# deve ser descartado para não apagar caractere errado nem inserir
# espaços extras na tela.
#
# Sequências de eco de apagar encontradas no VRP:
#   \x08 \x08   — BS + espaço + BS (padrão VTY classic)
#   \x08        — BS isolado (alguns firmwares)
#   \x1b[ND ... \x1b[ND — cursor-left + espaços + cursor-left (NE/ATN)
#
# IMPORTANTE: a ordem importa — o padrão \x1b[ND ... \x1b[ND deve vir
# ANTES do CSI genérico; \x08 \x08 antes do \x08 isolado.
_STRIP = re.compile(
    r"\x1b\[\d+D[ \x08]*\x1b\[\d+D"   # cursor-left + lixo + cursor-left
    r"|\x1b\[[0-9;]*[A-Za-z]"          # CSI sequences (cores, cursor)
    r"|\x1b\][^\x07]*\x07"             # OSC sequences (título)
    r"|\x1b[>=]"                        # DECPNM / DECPAM
    r"|\x08[ ]?\x08"                    # BS + [espaço] + BS
    r"|\x08"                            # BS isolado restante
)

# Fim de linha que é só o prompt do VRP (<nome> ou [nome...], com espaço
# opcional). Limita o backspace: dali para trás não se apaga.
_PROMPT_END = re.compile(r"[<\[][^<>\[\]\r\n]{0,80}[>\]] ?$")


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
        self._typed: str = ""    # buffer local: só o que está na linha atual

    def attach(self, chan) -> None:
        self._chan = chan

    def detach(self) -> None:
        self._chan = None

    # ------------------------------------------------------------------ #
    def focusNextPrevChild(self, _next: bool) -> bool:
        # Impede o Qt de usar Tab para trocar de widget — o Tab precisa
        # chegar ao keyPressEvent para virar autocomplete no VRP.
        return False

    def mouseReleaseEvent(self, event) -> None:
        # PuTTY: selecionar com o mouse já copia para a área de
        # transferência.
        super().mouseReleaseEvent(event)
        cursor = self.textCursor()
        if cursor.hasSelection():
            QGuiApplication.clipboard().setText(
                cursor.selectedText().replace("\u2029", "\n"))

    def mousePressEvent(self, event) -> None:
        # PuTTY: botão direito cola a área de transferência.
        if event.button() == Qt.MouseButton.RightButton:
            self._paste()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        # Sem menu de contexto — o botão direito é "colar", como no PuTTY.
        event.accept()

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self._send(text)
        # Mantém o buffer local coerente para histórico/backspace: o que
        # vier depois da última quebra de linha é a linha em digitação.
        if "\n" in text:
            self._typed = text.rsplit("\n", 1)[1]
        else:
            self._typed += text

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
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._typed = ""
            self._send("\n")
            return

        # ── Backspace ───────────────────────────────────────────────────
        # Apaga localmente de imediato (sem esperar o eco do equipamento)
        # para que o efeito visual seja instantâneo. O eco que o VRP devolve
        # (\x08 \x08 ou \x1b[1D \x1b[1D) é descartado pelo _STRIP, caso
        # contrário apagaria o caractere errado ou deixaria espaços extras.
        #
        # O limite NÃO é o buffer _typed (que não enxerga o que o Tab/
        # autocomplete acrescentou na linha) e sim o prompt: apaga até a
        # última linha da tela virar só "<nome>" / "[nome]".
        if key == Qt.Key.Key_Backspace:
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            if not _PROMPT_END.search(cursor.block().text()):
                cursor.deletePreviousChar()
                self.setTextCursor(cursor)
                self._typed = self._typed[:-1]
                self._send("\x08")
            return

        # ── ↑ / ↓ histórico ─────────────────────────────────────────────
        # Encaminha diretamente para o VRP como sequências ANSI — o
        # próprio equipamento gerencia o histórico (persiste entre
        # sessões, inclui comandos de outras conexões). Ao navegar, não
        # sabemos o que o VRP colocou na linha, então zeramos _typed para
        # o Backspace não achar que a linha está vazia antes do prompt.
        if key == Qt.Key.Key_Up:
            self._typed = ""
            self._send("\x1b[A")
            return
        if key == Qt.Key.Key_Down:
            self._typed = ""
            self._send("\x1b[B")
            return

        # ── Tab: autocomplete do VRP ────────────────────────────────────
        # O texto parcial já está na linha do equipamento; envia só o \t.
        if key == Qt.Key.Key_Tab:
            self._send("\t")
            return

        # ── Colar: Ctrl+V, Ctrl+Shift+V ou Shift+Insert ─────────────────
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if (key == Qt.Key.Key_V and ctrl) or \
                (key == Qt.Key.Key_Insert and shift):
            self._paste()
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
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
