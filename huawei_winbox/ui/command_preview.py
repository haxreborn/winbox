"""Pré-visualização do código CLI gerado pelo tradutor.

Antes de qualquer configuração ser enviada ao equipamento, esta janela
mostra exatamente os comandos VRP que serão executados — o usuário pode
revisar, copiar ou cancelar. É o coração da proposta "clicou, virou CLI".
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QApplication,
)


class CommandPreviewDialog(QDialog):
    """Mostra os comandos e pergunta se devem ser enviados via SSH."""

    def __init__(self, commands: list[str], parent=None,
                 title: str = "Pré-visualização dos comandos"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 320)
        self.commands = commands

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Os comandos abaixo foram gerados a partir dos seus cliques.\n"
            "Revise e confirme para enviá-los ao equipamento via SSH:"
        ))

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("cliPreview")
        self.editor.setPlainText("\n".join(["system-view", *commands, "return"]))
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("Copiar CLI")
        copy_btn.clicked.connect(self._copy)
        send_btn = QPushButton("Enviar para o equipamento")
        send_btn.setDefault(True)
        send_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(send_btn)
        layout.addLayout(buttons)

    def _copy(self):
        QApplication.clipboard().setText(self.editor.toPlainText())

    @staticmethod
    def confirm(commands: list[str], parent=None) -> bool:
        """Atalho: abre o diálogo e retorna True se o usuário confirmar."""
        dialog = CommandPreviewDialog(commands, parent)
        return dialog.exec() == QDialog.Accepted
