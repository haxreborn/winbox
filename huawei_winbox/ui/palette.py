"""Busca global (Ctrl+K) — abre qualquer módulo ou ação pelo nome.

Com mais de trinta módulos no menu lateral, procurar "onde fica o VRRP"
com o mouse custa mais que digitar. Aqui se digita parte do nome e o
primeiro resultado abre no Enter.

A busca casa por **subsequência**, não por prefixo: "trfg" encontra
"Tráfego (gráfico)" e "cfg" encontra "Configuração". Acentos são
ignorados, então "trafego" e "Tráfego" dão no mesmo. O que casa em
sequência contígua e no começo da palavra é pontuado melhor, para o
resultado óbvio ficar em primeiro.
"""

from __future__ import annotations

import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel,
)


def normalize(text: str) -> str:
    """Minúsculas sem acento — 'Endereços' e 'enderecos' se equivalem."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn")


def score(needle: str, haystack: str) -> int | None:
    """Pontuação da busca por subsequência (maior é melhor, None não casa).

    Percorre o alvo procurando as letras do termo em ordem. Cada letra que
    vem logo depois da anterior, ou que inicia uma palavra, vale mais — é o
    que faz "trafego" ganhar de um casamento espalhado por acaso.
    """
    if not needle:
        return 0
    needle = normalize(needle)
    target = normalize(haystack)
    if needle in target:
        # Casamento literal: prêmio grande, e maior ainda se for no começo.
        return 1000 - target.index(needle) * 2 - (len(target) - len(needle))

    position = 0
    total = 0
    streak = 0
    for char in needle:
        found = target.find(char, position)
        if found < 0:
            return None
        if found == position and position > 0:
            streak += 1
            total += 6 + streak          # letras contíguas valem progressivo
        else:
            streak = 0
            total += 1
        if found == 0 or (found > 0 and target[found - 1] in " -/()."):
            total += 8                   # início de palavra
        position = found + 1
    return total - (len(target) - len(needle)) // 4


class CommandPalette(QDialog):
    """Lista filtrável de módulos e ações."""

    def __init__(self, entries: list[tuple[str, str, object]], parent=None):
        """``entries`` são triplas ``(rótulo, categoria, callable)``."""
        super().__init__(parent)
        self.setWindowTitle("Ir para…")
        self.setModal(True)
        self.resize(520, 420)
        self._entries = entries

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Digite o nome do módulo ou da ação…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._accept_current)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _item: self._accept_current())
        self.list.itemClicked.connect(lambda _item: self._accept_current())
        layout.addWidget(self.list, 1)

        hint = QLabel("↑ ↓ para navegar · Enter para abrir · Esc para fechar")
        hint.setObjectName("itemCount")
        layout.addWidget(hint)

        self._filter("")
        self.search.setFocus()

    # ------------------------------------------------------------------ #
    def _filter(self, text: str) -> None:
        ranked = []
        for label, category, action in self._entries:
            value = score(text, f"{label} {category}")
            if value is not None:
                ranked.append((value, label, category, action))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        self.list.clear()
        for _value, label, category, action in ranked[:60]:
            item = QListWidgetItem(f"{label}      · {category}")
            item.setData(Qt.UserRole, action)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        action = item.data(Qt.UserRole)
        self.accept()
        if callable(action):
            action()

    def keyPressEvent(self, event) -> None:
        # As setas navegam a lista mesmo com o foco na caixa de busca.
        if event.key() in (Qt.Key_Down, Qt.Key_Up) and self.list.count():
            step = 1 if event.key() == Qt.Key_Down else -1
            row = (self.list.currentRow() + step) % self.list.count()
            self.list.setCurrentRow(row)
            return
        super().keyPressEvent(event)
