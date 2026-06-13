"""FlowLayout — layout que envolve widgets em múltiplas linhas (como word-wrap).

Port direto do exemplo C++ do Qt para Python/PySide6.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    """Layout que posiciona widgets em linha e quebra para a próxima quando
    não há mais espaço horizontal — como texto justificado."""

    def __init__(self, parent=None, h_spacing: int = 4, v_spacing: int = 4):
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 2, 0, 2)

    # ── QLayout interface ───────────────────────────────────────────── #
    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, dry_run=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ── layout engine ───────────────────────────────────────────────── #
    def _do_layout(self, rect: QRect, dry_run: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        row_h = 0

        for item in self._items:
            w = item.widget()
            hint = item.sizeHint()
            space_x = self._h_spacing
            space_y = self._v_spacing

            next_x = x + hint.width() + space_x
            if next_x - space_x > eff.right() and row_h > 0:
                x = eff.x()
                y += row_h + space_y
                next_x = x + hint.width() + space_x
                row_h = 0

            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            row_h = max(row_h, hint.height())

        return y + row_h - eff.y() + m.top() + m.bottom()
