"""Gráfico de tráfego desenhado com QPainter (sem dependências externas).

Mostra duas séries no tempo — RX (verde) e TX (azul) em bits/s — com
escala automática, grade, rótulos legíveis (Kbps/Mbps/Gbps) e legenda com
valor atual e pico. Mantém o visual claro do WinBox e não exige matplotlib.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPolygonF, QFont, QLinearGradient,
)
from PySide6.QtWidgets import QWidget

from ...core.parsers import format_bps

_RX_COLOR = QColor("#2e8b57")   # verde (entrada)
_TX_COLOR = QColor("#316ac5")   # azul  (saída)
_GRID = QColor("#dcdcdc")
_AXIS_TEXT = QColor("#555555")
_TITLE = QColor("#223344")
_BG = QColor("#ffffff")
_BORDER = QColor("#8a8a8a")


def _nice_ceiling(value: float) -> float:
    """Arredonda para cima a um valor "redondo" (1/2/2.5/5 × 10ⁿ)."""
    if value <= 0:
        return 1000.0
    exp = math.floor(math.log10(value))
    base = 10.0 ** exp
    for mult in (1, 2, 2.5, 5, 10):
        if value <= mult * base:
            return mult * base
    return 10 * base


class TrafficGraphWidget(QWidget):
    """Plota séries (timestamp, rx_bps, tx_bps) de uma interface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setAutoFillBackground(False)
        self._samples: list[tuple[float, float, float]] = []
        self._window: float | None = None     # segundos; None = tudo
        self._title = ""

    def set_samples(self, samples, window_seconds=None) -> None:
        self._samples = list(samples)
        self._window = window_seconds
        self.update()

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    # ------------------------------------------------------------------ #
    def _visible(self) -> list[tuple[float, float, float]]:
        if not self._samples:
            return []
        if not self._window:
            return self._samples
        t_end = self._samples[-1][0]
        t_start = t_end - self._window
        return [s for s in self._samples if s[0] >= t_start] or self._samples[-2:]

    def paintEvent(self, event) -> None:  # noqa: C901 — desenho linear
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()
        left, right, top, bottom = 70, 14, 26, 24
        pw = max(1, w - left - right)
        ph = max(1, h - top - bottom)
        x0, y0 = left, top
        x1, y1 = left + pw, top + ph

        # Fundo e moldura da área de plotagem
        p.fillRect(self.rect(), _BG)
        p.setPen(QPen(_BORDER, 1))
        p.drawRect(x0, y0, pw, ph)

        # Título
        if self._title:
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            p.setPen(_TITLE)
            p.drawText(x0, 2, w - x0, top - 4,
                       Qt.AlignVCenter | Qt.AlignLeft, self._title)

        samples = self._visible()
        if len(samples) < 2:
            p.setPen(_AXIS_TEXT)
            p.drawText(x0, y0, pw, ph, Qt.AlignCenter,
                       "Aguardando dados...\n(inicie a coleta)")
            p.end()
            return

        t_start = samples[0][0]
        t_end = samples[-1][0]
        span = max(0.001, t_end - t_start)
        peak_rx = max(s[1] for s in samples)
        peak_tx = max(s[2] for s in samples)
        ceiling = _nice_ceiling(max(peak_rx, peak_tx))

        def px(t: float) -> float:
            return x0 + (t - t_start) / span * pw

        def py(rate: float) -> float:
            return y1 - min(rate, ceiling) / ceiling * ph

        # ── Grade horizontal + rótulos de banda ───────────────────────── #
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        p.setFont(small)
        divisions = 4
        for i in range(divisions + 1):
            frac = i / divisions
            y = y1 - frac * ph
            p.setPen(QPen(_GRID, 1))
            if i:
                p.drawLine(int(x0) + 1, int(y), int(x1) - 1, int(y))
            p.setPen(_AXIS_TEXT)
            p.drawText(0, int(y) - 8, left - 6, 16,
                       Qt.AlignRight | Qt.AlignVCenter,
                       format_bps(ceiling * frac))

        # ── Grade vertical + rótulos de tempo (relativos) ─────────────── #
        # Unidade única para todo o eixo (evita misturar "s" e "min").
        use_min = span >= 120
        for i in range(divisions + 1):
            frac = i / divisions
            x = x0 + frac * pw
            if i and i < divisions:
                p.setPen(QPen(_GRID, 1))
                p.drawLine(int(x), int(y0) + 1, int(x), int(y1) - 1)
            secs = (1 - frac) * span
            if i == divisions or secs < 1:
                label = "agora"
            elif use_min:
                label = f"-{secs / 60:.0f}min"
            else:
                label = f"-{secs:.0f}s"
            p.setPen(_AXIS_TEXT)
            # Ancora os rótulos das pontas para dentro da área (senão
            # "agora" na borda direita fica cortado).
            if i == 0:
                box_x, align = int(x), Qt.AlignLeft
            elif i == divisions:
                box_x, align = int(x) - 60, Qt.AlignRight
            else:
                box_x, align = int(x) - 30, Qt.AlignHCenter
            p.drawText(box_x, y1 + 4, 60, bottom - 4,
                       align | Qt.AlignTop, label)

        # ── Áreas + linhas (RX por baixo, TX por cima) ────────────────── #
        def draw_series(idx: int, color: QColor):
            line = QPolygonF([QPointF(px(s[0]), py(s[idx])) for s in samples])
            area = QPolygonF(line)
            area.append(QPointF(px(t_end), y1))
            area.append(QPointF(px(t_start), y1))
            grad = QLinearGradient(0, y0, 0, y1)
            fill = QColor(color)
            fill.setAlpha(70)
            grad.setColorAt(0.0, fill)
            tail = QColor(color)
            tail.setAlpha(18)
            grad.setColorAt(1.0, tail)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPolygon(area)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 1.6))
            p.drawPolyline(line)

        p.setClipRect(x0, y0, pw, ph)
        draw_series(1, _RX_COLOR)
        draw_series(2, _TX_COLOR)
        p.setClipping(False)

        # ── Legenda (atual / pico) ────────────────────────────────────── #
        cur_rx, cur_tx = samples[-1][1], samples[-1][2]
        self._legend(p, x0 + 8, y0 + 6, _RX_COLOR,
                     f"RX  {format_bps(cur_rx)}   pico {format_bps(peak_rx)}")
        self._legend(p, x0 + 8, y0 + 22, _TX_COLOR,
                     f"TX  {format_bps(cur_tx)}   pico {format_bps(peak_tx)}")
        p.end()

    @staticmethod
    def _legend(p: QPainter, x: int, y: int, color: QColor, text: str) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRect(x, y + 2, 10, 10)
        p.setPen(QColor("#1c1c1c"))
        p.drawText(x + 16, y, 260, 14, Qt.AlignLeft | Qt.AlignVCenter, text)
