"""Terminal interativo — conexão SSH dedicada + polling em tempo real.

Cada abertura do painel cria uma CONEXÃO SSH própria (equipamentos VRP
limitam canais por conexão SSH; um segundo invoke_shell na mesma conexão
ficaria bloqueado para sempre, congelando o aplicativo). A conexão é
aberta em background — a janela nunca trava. Não há lock compartilhado,
não há espera de prompt: o QTimer de 40 ms lê tudo que chegar do
equipamento e exibe imediatamente — exatamente como o PuTTY faz.

Recursos:
- Saída em streaming (sem esperar o prompt para mostrar)
- Prompts interativos funcionam (ex.: confirmação do save "y/n")
- Histórico de comandos com ↑/↓
- Tab completa no terminal (enviado para o dispositivo)
- Ctrl+C, Ctrl+Z e outros control codes
"""

from __future__ import annotations

import re
import socket

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QLabel, QPushButton,
)

from ..worker import run_async

# Sequências ANSI que causam ruído visual (mantém texto, remove formatação).
_STRIP = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]"     # CSI sequences: cores, movimento de cursor
    r"|\x1b\][^\x07]*\x07"        # OSC sequences: título da janela
    r"|\x1b[>=]"                  # DECPNM / DECPAM
    r"|\x1b\[16D\s*\x1b\[16D"    # VRP backspace-erase pattern
    r"|\x08+"                     # backspace characters
)


class TerminalPanel(QWidget):
    title = "Terminal"

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._history: list[str] = []
        self._hist_pos = 0
        self._client = None      # SSHClient dedicado deste terminal
        self._chan = None
        self._closing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ── saída ──────────────────────────────────────────────────────
        self.output = QPlainTextEdit()
        self.output.setObjectName("terminal")
        self.output.setReadOnly(True)
        # Limita o buffer a 5 000 linhas para não crescer infinitamente.
        self.output.setMaximumBlockCount(5000)
        root.addWidget(self.output)

        # ── linha de entrada ───────────────────────────────────────────
        bar = QHBoxLayout()
        self.prompt_lbl = QLabel(f"<{session.hostname}>")
        self.prompt_lbl.setMinimumWidth(120)
        bar.addWidget(self.prompt_lbl)

        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "comando VRP — Enter envia  ·  ↑/↓ histórico  ·  Tab autocompleta")
        self.input.returnPressed.connect(self._send)
        self.input.installEventFilter(self)
        bar.addWidget(self.input, 1)

        root.addLayout(bar)

        # ── canal SSH dedicado ─────────────────────────────────────────
        self._open_channel()

        # ── polling: lê o canal a cada 40 ms ──────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(40)

        self.input.setFocus()

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    def _open_channel(self) -> None:
        """Abre a conexão SSH dedicada em background (UI nunca trava)."""
        self._append(
            f"Abrindo conexão SSH dedicada com {self.session.host}...\n")

        def done(result):
            client, chan = result
            if self._closing:
                # Janela foi fechada antes da conexão completar.
                try:
                    chan.close()
                    client.close()
                except Exception:
                    pass
                return
            self._client, self._chan = client, chan
            self._append(
                f"Conectado a {self.session.hostname} "
                f"({self.session.host}) — conexão exclusiva do terminal.\n\n")
            self.input.setFocus()

        def fail(message: str):
            self._append(
                f"[Falha ao abrir terminal: {message}]\n\n"
                "Feche e reabra esta janela para tentar novamente.\n")

        run_async(self.session.open_terminal, done, fail)

    def closeEvent(self, event) -> None:
        """Fecha o timer, o canal e a conexão dedicada ao destruir o painel."""
        self._closing = True
        self._timer.stop()
        for obj in (self._chan, self._client):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # Polling / saída
    # ------------------------------------------------------------------ #

    def _poll(self) -> None:
        """Chamado pelo QTimer: lê o que o equipamento enviou e exibe."""
        if not self._chan or self._chan.closed:
            return
        try:
            data = self._chan.recv(4096)
        except socket.timeout:
            return
        except Exception:
            return
        if not data:
            return

        # Decodifica e remove sequências ANSI problemáticas.
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        text = _STRIP.sub("", text)
        if not text:
            return

        self._append(text)

        # Atualiza o label com o último prompt recebido, exatamente como
        # o equipamento o exibe (<PROM-MAR-BGP-SW01>, [PROM-...-vlan10]...).
        prompts = re.findall(r"[<\[]\S[^<>\[\]\r\n]{0,79}[>\]]", text)
        if prompts:
            self.prompt_lbl.setText(prompts[-1])

    def _append(self, text: str) -> None:
        """Insere texto no final do painel sem adicionar newline extra."""
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    # ------------------------------------------------------------------ #
    # Envio de comandos
    # ------------------------------------------------------------------ #

    def _send(self) -> None:
        if not self._chan or self._chan.closed:
            self._append(
                "\n[Canal fechado — feche e reabra o Terminal]\n")
            return
        text = self.input.text()
        if text.strip():
            self._history.append(text)
        self._hist_pos = len(self._history)
        self.input.clear()
        try:
            self._chan.send(text + "\n")
        except Exception as exc:
            self._append(f"\n[Erro ao enviar: {exc}]\n")

    def _send_raw(self, data: bytes) -> None:
        """Envia bytes brutos (ex.: Ctrl+C = b'\\x03')."""
        if self._chan and not self._chan.closed:
            try:
                self._chan.send(data)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Event filter — ↑/↓ histórico, Tab, Ctrl+C
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        if obj is not self.input:
            return super().eventFilter(obj, event)
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()
        mods = event.modifiers()

        # ↑/↓ — histórico
        if key == Qt.Key.Key_Up:
            if self._history and self._hist_pos > 0:
                self._hist_pos -= 1
                self.input.setText(self._history[self._hist_pos])
            return True
        if key == Qt.Key.Key_Down:
            if self._hist_pos < len(self._history) - 1:
                self._hist_pos += 1
                self.input.setText(self._history[self._hist_pos])
            else:
                self._hist_pos = len(self._history)
                self.input.clear()
            return True

        # Tab — envia para o equipamento (autocomplete do VRP)
        if key == Qt.Key.Key_Tab:
            cmd = self.input.text()
            self._send_raw((cmd + "\t").encode())
            self.input.clear()
            return True

        # Ctrl+C — interrompe comando em execução no VRP
        if key == Qt.Key.Key_C and mods == Qt.KeyboardModifier.ControlModifier:
            self._send_raw(b"\x03")
            self.input.clear()
            return True

        # Ctrl+Z
        if key == Qt.Key.Key_Z and mods == Qt.KeyboardModifier.ControlModifier:
            self._send_raw(b"\x1a")
            return True

        return super().eventFilter(obj, event)
