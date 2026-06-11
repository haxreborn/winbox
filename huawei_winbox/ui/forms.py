"""Formulários genéricos simples (rótulo + campo), usados pelos painéis."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QSpinBox, QComboBox, QCheckBox,
    QDialogButtonBox,
)


class SimpleForm(QDialog):
    """Diálogo de formulário declarativo.

    fields: lista de tuplas (chave, rótulo, tipo, default), onde tipo é
    "text", "int", "choice:a|b|c" ou "bool".
    """

    def __init__(self, title: str, fields: list[tuple], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QFormLayout(self)
        self._widgets = {}

        for key, label, kind, default in fields:
            if kind == "int":
                w = QSpinBox()
                w.setRange(0, 2147483647 if key.endswith("_as") else 1000000)
                w.setValue(int(default or 0))
            elif kind.startswith("choice:"):
                w = QComboBox()
                w.addItems(kind.split(":", 1)[1].split("|"))
                if default:
                    w.setCurrentText(str(default))
            elif kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(default))
            else:
                w = QLineEdit(str(default or ""))
            self._widgets[key] = w
            layout.addRow(label, w)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict:
        out = {}
        for key, w in self._widgets.items():
            if isinstance(w, QSpinBox):
                out[key] = w.value()
            elif isinstance(w, QComboBox):
                out[key] = w.currentText()
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    @staticmethod
    def ask(title: str, fields: list[tuple], parent=None) -> dict | None:
        form = SimpleForm(title, fields, parent)
        if form.exec() == QDialog.Accepted:
            return form.values()
        return None
