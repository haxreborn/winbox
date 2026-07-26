"""Gráficos de telemetria desenhados com QPainter (sem dependências extras).

``TrafficGraphWidget`` plota **quantas séries forem necessárias** ao mesmo
tempo — era a limitação principal do gráfico antigo, que só sabia desenhar
uma interface. Há três modos de leitura:

``sobreposto``
    Todas as interfaces no mesmo par de eixos. Bom para comparar quem
    está consumindo mais.
``grade``
    Um mini-gráfico por interface, alinhados no mesmo eixo de tempo
    (*small multiples*). Bom para varrer muitas portas de uma vez sem que
    as linhas se embolem.
``empilhado``
    Áreas somadas, mostrando a composição do tráfego total.

Em qualquer modo, o cursor do mouse traça uma linha vertical e mostra o
valor de **todas** as séries naquele instante.

``MiniChart`` é a versão reduzida (sparkline) usada no Painel de visão
geral para CPU, memória e total de banda.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPolygonF, QFont, QLinearGradient,
    QFontMetrics, QIcon, QPixmap,
)
from PySide6.QtWidgets import QWidget

from ...core.parsers import format_bps

MODE_OVERLAY = "sobreposto"
MODE_GRID = "grade"
MODE_STACKED = "empilhado"
MODES = [MODE_OVERLAY, MODE_GRID, MODE_STACKED]

DIR_BOTH = "RX + TX"
DIR_RX = "Somente RX"
DIR_TX = "Somente TX"
DIRECTIONS = [DIR_BOTH, DIR_RX, DIR_TX]

SCALE_SHARED = "Escala comum"
SCALE_PER_PLOT = "Escala por gráfico"
SCALES = [SCALE_SHARED, SCALE_PER_PLOT]

# Paleta categórica legível sobre fundo claro. As cores foram escolhidas
# para continuarem distinguíveis quando várias linhas se cruzam; a variante
# clara de cada uma é usada para o TX da mesma interface.
PALETTE = [
    "#1f6fb2", "#2e8b57", "#c0392b", "#d08a1f", "#7d3c98",
    "#0e8a8a", "#c2377e", "#6b8e23", "#34495e", "#d35400",
    "#2980b9", "#16a085", "#8e6c1f", "#a93226", "#5b2c6f",
    "#117864", "#943126", "#4a235a", "#1a5276", "#7e5109",
]

_GRID = QColor("#dcdcdc")
_GRID_SOFT = QColor("#eeeeee")
_AXIS_TEXT = QColor("#555555")
_TITLE = QColor("#223344")
_BG = QColor("#ffffff")
_PANEL_BG = QColor("#fafbfc")
_BORDER = QColor("#8a8a8a")
_CURSOR = QColor("#e2001a")
_TIP_BG = QColor(255, 255, 255, 244)
_TIP_BORDER = QColor("#8a8a8a")


def series_color(index: int) -> QColor:
    return QColor(PALETTE[index % len(PALETTE)])


_SWATCH_CACHE: dict[str, QIcon] = {}


def swatch_icon(color: QColor, size: int = 11) -> QIcon:
    """Quadradinho colorido para ligar a linha do gráfico à da tabela."""
    key = f"{color.name()}:{size}"
    icon = _SWATCH_CACHE.get(key)
    if icon is None:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(color.darker(140), 1))
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 2, 2)
        painter.end()
        icon = QIcon(pixmap)
        _SWATCH_CACHE[key] = icon
    return icon


def _lighter(color: QColor) -> QColor:
    """Variante clara usada para o TX (o RX fica com a cor cheia)."""
    return QColor(color).lighter(155)


def axis_label(bps: float) -> str:
    """Rótulo curto para o eixo Y ('0', '200M', '1.5G').

    O eixo não tem espaço para "200.00 Mbps" — a forma longa fica na
    legenda e no balão do cursor, onde há largura de sobra.
    """
    if bps <= 0:
        return "0"
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if bps >= limit:
            value = bps / limit
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{bps:.0f}"


def _nice_ceiling(value: float) -> float:
    """Arredonda para cima a um valor "redondo" (1/2/2.5/5 × 10ⁿ)."""
    if value <= 0:
        return 1000.0
    exp = math.floor(math.log10(value))
    base = 10.0 ** exp
    for mult in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= mult * base:
            return mult * base
    return 10 * base


class Series:
    """Uma interface no gráfico: rótulo, cor, amostras e visibilidade."""

    __slots__ = ("key", "label", "color", "samples", "visible",
                 "speed_bps", "highlight")

    def __init__(self, key: str, label: str = "", color: QColor | None = None,
                 samples=None, visible: bool = True, speed_bps: int = 0):
        self.key = key
        self.label = label or key
        self.color = color or QColor(PALETTE[0])
        self.samples = samples or []
        self.visible = visible
        self.speed_bps = speed_bps
        self.highlight = False

    def value_at(self, t: float) -> tuple[float, float] | None:
        """Último valor conhecido em (ou antes de) ``t``."""
        if not self.samples:
            return None
        lo, hi = 0, len(self.samples)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.samples[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid
        index = max(0, lo - 1)
        row = self.samples[index]
        return row[1], row[2]


class TrafficGraphWidget(QWidget):
    """Plota várias séries de tráfego simultaneamente."""

    seriesClicked = Signal(str)          # chave da série sob o cursor

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._series: list[Series] = []
        self._window: float | None = 300.0
        self._mode = MODE_OVERLAY
        self._direction = DIR_BOTH
        self._scale = SCALE_SHARED
        self._title = ""
        self._subtitle = ""
        self._cursor_x: float | None = None
        self._plots: list[tuple[QRectF, list[Series]]] = []
        self._t_start = 0.0
        self._t_end = 0.0
        self._fixed_ceiling: float | None = None
        self._show_legend = True
        # Camada desenhada (eixos + séries) guardada num pixmap. Redesenhar
        # dezenas de séries antialiasadas custa centenas de milissegundos;
        # sem este cache, mover o mouse sobre o gráfico — que só precisa da
        # linha do cursor — repintaria tudo e a janela pareceria travada.
        self._layer: QPixmap | None = None
        self._layer_key: tuple | None = None
        self._revision = 0

    def _invalidate(self) -> None:
        self._revision += 1
        self.update()

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    def set_series(self, series: list[Series]) -> None:
        self._series = list(series)
        self._invalidate()

    def set_window(self, seconds: float | None) -> None:
        self._window = seconds
        self._invalidate()

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in MODES else MODE_OVERLAY
        self._invalidate()

    def set_direction(self, direction: str) -> None:
        self._direction = direction if direction in DIRECTIONS else DIR_BOTH
        self._invalidate()

    def set_scale(self, scale: str) -> None:
        self._scale = scale if scale in SCALES else SCALE_SHARED
        self._invalidate()

    def set_fixed_ceiling(self, ceiling: float | None) -> None:
        self._fixed_ceiling = ceiling
        self._invalidate()

    def set_title(self, title: str, subtitle: str = "") -> None:
        self._title = title
        self._subtitle = subtitle
        self._invalidate()

    def set_legend_visible(self, visible: bool) -> None:
        self._show_legend = visible
        self._invalidate()

    def set_highlight(self, keys) -> None:
        """Realça as séries indicadas (seleção na tabela do painel)."""
        wanted = set(keys)
        changed = False
        for series in self._series:
            highlight = series.key in wanted
            if series.highlight != highlight:
                series.highlight = highlight
                changed = True
        if changed:
            self._invalidate()

    def visible_series(self) -> list[Series]:
        return [s for s in self._series if s.visible and s.samples]

    # ------------------------------------------------------------------ #
    # Cálculo do domínio de tempo e escala
    # ------------------------------------------------------------------ #
    def _time_range(self, series: list[Series]) -> tuple[float, float]:
        ends = [s.samples[-1][0] for s in series if s.samples]
        starts = [s.samples[0][0] for s in series if s.samples]
        if not ends:
            now = time.time()
            return now - (self._window or 60.0), now
        t_end = max(ends)
        if self._window:
            t_start = t_end - self._window
        else:
            t_start = min(starts)
        if t_end - t_start < 1.0:
            t_start = t_end - 1.0
        return t_start, t_end

    def _peak(self, series: list[Series], t_start: float) -> float:
        peak = 0.0
        show_rx = self._direction in (DIR_BOTH, DIR_RX)
        show_tx = self._direction in (DIR_BOTH, DIR_TX)
        for s in series:
            for t, rx, tx in s.samples:
                if t < t_start:
                    continue
                if show_rx and rx > peak:
                    peak = rx
                if show_tx and tx > peak:
                    peak = tx
        return peak

    def _stacked_peak(self, series: list[Series], t_start: float) -> float:
        totals: dict[float, float] = {}
        show_rx = self._direction in (DIR_BOTH, DIR_RX)
        show_tx = self._direction in (DIR_BOTH, DIR_TX)
        for s in series:
            for t, rx, tx in s.samples:
                if t < t_start:
                    continue
                value = (rx if show_rx else 0.0) + (tx if show_tx else 0.0)
                totals[t] = totals.get(t, 0.0) + value
        return max(totals.values(), default=0.0)

    # ------------------------------------------------------------------ #
    # Desenho
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:
        self._ensure_layer()
        painter = QPainter(self)
        if self._layer is not None:
            painter.drawPixmap(0, 0, self._layer)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_cursor(painter)
        painter.end()

    def _ensure_layer(self) -> None:
        """(Re)desenha a camada de eixos e séries só quando algo mudou."""
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), round(ratio, 2), self._revision)
        if self._layer is not None and self._layer_key == key:
            return
        pixmap = QPixmap(max(1, int(self.width() * ratio)),
                         max(1, int(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(_BG)
        painter = QPainter(pixmap)
        # Acima de ~16 linhas sobrepostas o antisserrilhado só cobra caro:
        # o desenho já está denso demais para que a suavização se note.
        painter.setRenderHint(QPainter.Antialiasing,
                              len(self.visible_series()) <= 16)
        self._render_layer(painter)
        painter.end()
        self._layer = pixmap
        self._layer_key = key

    def _render_layer(self, painter: QPainter) -> None:
        series = self.visible_series()
        top = 4
        if self._title:
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(_TITLE)
            painter.drawText(8, top, self.width() - 16, 16,
                             Qt.AlignVCenter | Qt.AlignLeft, self._title)
            top += 18
        if self._subtitle:
            painter.setPen(_AXIS_TEXT)
            painter.drawText(8, top, self.width() - 16, 14,
                             Qt.AlignVCenter | Qt.AlignLeft, self._subtitle)
            top += 15

        area = QRectF(0, top, self.width(), max(40, self.height() - top - 2))
        self._plots = []

        if not series:
            painter.setPen(_AXIS_TEXT)
            painter.drawText(area, Qt.AlignCenter,
                             "Selecione uma ou mais interfaces na lista\n"
                             "e inicie a coleta.")
            return

        self._t_start, self._t_end = self._time_range(series)

        if self._mode == MODE_GRID:
            self._paint_grid(painter, area, series)
        else:
            self._paint_single(painter, area, series)

    # -- modo sobreposto / empilhado ----------------------------------- #
    def _paint_single(self, painter: QPainter, area: QRectF,
                      series: list[Series]) -> None:
        rect = self._plot_rect(area)
        if self._mode == MODE_STACKED:
            peak = self._stacked_peak(series, self._t_start)
        else:
            peak = self._peak(series, self._t_start)
        ceiling = self._fixed_ceiling or _nice_ceiling(peak)
        self._draw_axes(painter, rect, ceiling, time_labels=True)
        painter.save()
        painter.setClipRect(rect)
        if self._mode == MODE_STACKED:
            self._draw_stack(painter, rect, series, ceiling)
        else:
            # Séries destacadas por último: ficam por cima das demais.
            for s in sorted(series, key=lambda s: s.highlight):
                self._draw_series(painter, rect, s, ceiling,
                                  fill=len(series) == 1)
        painter.restore()
        self._plots.append((rect, series))

    # -- modo grade (small multiples) ---------------------------------- #
    def _paint_grid(self, painter: QPainter, area: QRectF,
                    series: list[Series]) -> None:
        count = len(series)
        columns = max(1, min(count, int(math.ceil(math.sqrt(count * 1.6)))))
        rows = int(math.ceil(count / columns))
        gap = 6.0
        cell_w = (area.width() - gap * (columns - 1)) / columns
        cell_h = (area.height() - gap * (rows - 1)) / rows
        shared_ceiling = None
        if self._scale == SCALE_SHARED:
            shared_ceiling = self._fixed_ceiling or _nice_ceiling(
                self._peak(series, self._t_start))

        # Mostrar os valores em algumas células e em outras não (porque o
        # texto de uma era mais curto) fica visualmente irregular: a decisão
        # é uma só para a grade inteira, pela célula mais exigente.
        header_font = QFont(self.font())
        header_font.setPointSizeF(max(6.5, self.font().pointSizeF() - 1))
        header_font.setBold(True)
        metrics = QFontMetrics(header_font)
        widest = max((metrics.horizontalAdvance(_cell_values(s))
                      for s in series), default=0)
        show_values = (cell_w - 44 - widest - 6
                       >= metrics.horizontalAdvance("XXXXXXXX"))

        for index, s in enumerate(series):
            row, col = divmod(index, columns)
            cell = QRectF(area.left() + col * (cell_w + gap),
                          area.top() + row * (cell_h + gap),
                          cell_w, cell_h)
            ceiling = shared_ceiling or _nice_ceiling(
                self._peak([s], self._t_start))
            rect = self._plot_rect(cell, compact=True,
                                   time_labels=row == rows - 1)
            painter.fillRect(cell, _PANEL_BG)
            self._draw_axes(painter, rect, ceiling, compact=True,
                            time_labels=row == rows - 1)
            painter.save()
            painter.setClipRect(rect)
            self._draw_series(painter, rect, s, ceiling, fill=True)
            painter.restore()
            self._draw_cell_header(painter, cell, rect, s, header_font,
                                   show_values, widest)
            self._plots.append((rect, [s]))

    def _draw_cell_header(self, painter: QPainter, cell: QRectF, rect: QRectF,
                          s: Series, font: QFont, show_values: bool,
                          value_w: float) -> None:
        """Nome da porta à esquerda e valores à direita, sem se sobreporem.

        Numa grade densa a célula fica estreita; quando nome e valores não
        cabem lado a lado, os valores saem de TODAS as células e o nome é
        abreviado no meio — antes os dois eram desenhados um por cima do
        outro.
        """
        painter.setFont(font)
        metrics = QFontMetrics(font)
        band = QRectF(cell.left() + 2, cell.top() + 1, cell.width() - 4, 14)
        name_w = band.width() - (value_w + 8 if show_values else 0)

        painter.setPen(s.color.darker(125))
        painter.drawText(QRectF(band.left(), band.top(), name_w, band.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(s.label, Qt.ElideMiddle,
                                            int(max(10.0, name_w - 4))))
        if show_values:
            painter.setPen(_AXIS_TEXT)
            painter.drawText(QRectF(band.right() - value_w, band.top(),
                                    value_w, band.height()),
                             Qt.AlignRight | Qt.AlignVCenter, _cell_values(s))

    # -- primitivas ---------------------------------------------------- #
    def _plot_rect(self, area: QRectF, compact: bool = False,
                   time_labels: bool = True) -> QRectF:
        left = 44 if not compact else 34
        right = 10
        top = 6 if not compact else 16
        bottom = 20 if (time_labels and not compact) else (
            16 if time_labels else 4)
        return QRectF(area.left() + left, area.top() + top,
                      max(20.0, area.width() - left - right),
                      max(20.0, area.height() - top - bottom))

    def _draw_axes(self, painter: QPainter, rect: QRectF, ceiling: float,
                   compact: bool = False, time_labels: bool = True) -> None:
        painter.setPen(QPen(_BORDER, 1))
        painter.drawRect(rect)
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, self.font().pointSizeF() - 1))
        painter.setFont(font)

        divisions = 2 if compact else 4
        for i in range(divisions + 1):
            frac = i / divisions
            y = rect.bottom() - frac * rect.height()
            if i:
                painter.setPen(QPen(_GRID if not compact else _GRID_SOFT, 1))
                painter.drawLine(QPointF(rect.left() + 1, y),
                                 QPointF(rect.right() - 1, y))
            painter.setPen(_AXIS_TEXT)
            label_left = max(0.0, rect.left() - 42)
            painter.drawText(QRectF(label_left, y - 8,
                                    rect.left() - label_left - 4, 16),
                             Qt.AlignRight | Qt.AlignVCenter,
                             axis_label(ceiling * frac))

        span = max(0.001, self._t_end - self._t_start)
        steps = 2 if compact else 4
        for i in range(steps + 1):
            frac = i / steps
            x = rect.left() + frac * rect.width()
            if 0 < i < steps:
                painter.setPen(QPen(_GRID if not compact else _GRID_SOFT, 1))
                painter.drawLine(QPointF(x, rect.top() + 1),
                                 QPointF(x, rect.bottom() - 1))
            if not time_labels:
                continue
            seconds = (1 - frac) * span
            label = _elapsed_label(seconds, span)
            painter.setPen(_AXIS_TEXT)
            if i == 0:
                box, align = QRectF(x, rect.bottom() + 2, 64, 14), Qt.AlignLeft
            elif i == steps:
                box, align = (QRectF(x - 64, rect.bottom() + 2, 64, 14),
                              Qt.AlignRight)
            else:
                box, align = (QRectF(x - 32, rect.bottom() + 2, 64, 14),
                              Qt.AlignHCenter)
            painter.drawText(box, align | Qt.AlignTop, label)

    def _points(self, rect: QRectF, samples, index: int,
                ceiling: float) -> QPolygonF:
        # Montar o polígono numa lista e convertê-lo de uma vez é bem mais
        # barato que chamar append() ponto a ponto: cada append cruza a
        # ponte Python↔Qt, e com dezenas de séries isso dominava o tempo de
        # desenho (o gráfico ficava com uma taxa de quadros visivelmente
        # travada justamente quando havia mais coisa para ver).
        t_start = self._t_start
        span = max(0.001, self._t_end - t_start)
        scale_x = rect.width() / span
        left = rect.left()
        bottom = rect.bottom()
        scale_y = rect.height() / ceiling
        return QPolygonF([
            QPointF(left + (row[0] - t_start) * scale_x,
                    bottom - min(row[index], ceiling) * scale_y)
            for row in samples if row[0] >= t_start])

    def _draw_series(self, painter: QPainter, rect: QRectF, s: Series,
                     ceiling: float, fill: bool = False) -> None:
        width = 2.2 if s.highlight else 1.5
        if self._direction in (DIR_BOTH, DIR_RX):
            self._draw_line(painter, rect, s.samples, 1, ceiling, s.color,
                            width, fill)
        if self._direction in (DIR_BOTH, DIR_TX):
            color = _lighter(s.color) if self._direction == DIR_BOTH else s.color
            self._draw_line(painter, rect, s.samples, 2, ceiling, color,
                            width, fill, dashed=self._direction == DIR_BOTH)

    def _draw_line(self, painter: QPainter, rect: QRectF, samples, index: int,
                   ceiling: float, color: QColor, width: float,
                   fill: bool, dashed: bool = False) -> None:
        poly = self._points(rect, samples, index, ceiling)
        if poly.count() < 2:
            return
        if fill:
            area = QPolygonF(poly)
            area.append(QPointF(poly.last().x(), rect.bottom()))
            area.append(QPointF(poly.first().x(), rect.bottom()))
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            top_color = QColor(color)
            top_color.setAlpha(70)
            grad.setColorAt(0.0, top_color)
            tail = QColor(color)
            tail.setAlpha(16)
            grad.setColorAt(1.0, tail)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPolygon(area)
            painter.setBrush(Qt.NoBrush)
        pen = QPen(color, width)
        if dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawPolyline(poly)

    def _draw_stack(self, painter: QPainter, rect: QRectF,
                    series: list[Series], ceiling: float) -> None:
        """Áreas somadas sobre a grade de tempo comum das amostras."""
        timestamps = sorted({row[0] for s in series for row in s.samples
                             if row[0] >= self._t_start})
        if len(timestamps) < 2:
            return
        # Empilhar exige avaliar cada série em cada instante; acima de uma
        # coluna de pixels por ponto isso é trabalho jogado fora.
        limit = max(120, int(rect.width()))
        if len(timestamps) > limit:
            step = (len(timestamps) + limit - 1) // limit
            last = timestamps[-1]
            timestamps = timestamps[::step]
            if timestamps[-1] != last:
                timestamps.append(last)
        show_rx = self._direction in (DIR_BOTH, DIR_RX)
        show_tx = self._direction in (DIR_BOTH, DIR_TX)
        span = max(0.001, self._t_end - self._t_start)
        base = [0.0] * len(timestamps)
        for s in series:
            top = []
            for i, t in enumerate(timestamps):
                pair = s.value_at(t) or (0.0, 0.0)
                value = (pair[0] if show_rx else 0.0) + \
                        (pair[1] if show_tx else 0.0)
                top.append(base[i] + value)
            poly = QPolygonF()
            for i, t in enumerate(timestamps):
                x = rect.left() + (t - self._t_start) / span * rect.width()
                y = rect.bottom() - min(top[i], ceiling) / ceiling * rect.height()
                poly.append(QPointF(x, y))
            for i in range(len(timestamps) - 1, -1, -1):
                t = timestamps[i]
                x = rect.left() + (t - self._t_start) / span * rect.width()
                y = rect.bottom() - min(base[i], ceiling) / ceiling * rect.height()
                poly.append(QPointF(x, y))
            fill = QColor(s.color)
            fill.setAlpha(190 if s.highlight else 150)
            painter.setPen(QPen(s.color.darker(115), 0.8))
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(poly)
            painter.setBrush(Qt.NoBrush)
            base = top

    # -- cursor / tooltip ---------------------------------------------- #
    def _paint_cursor(self, painter: QPainter) -> None:
        if self._cursor_x is None or not self._plots:
            return
        target = None
        for rect, series in self._plots:
            if rect.left() <= self._cursor_x <= rect.right():
                target = (rect, series)
                break
        if target is None:
            return
        rect, _series = target
        span = max(0.001, self._t_end - self._t_start)
        frac = (self._cursor_x - rect.left()) / rect.width()
        t = self._t_start + frac * span

        painter.setPen(QPen(_CURSOR, 1, Qt.DashLine))
        for r, _s in self._plots:
            if abs(r.left() - rect.left()) < 0.5 or self._mode != MODE_GRID:
                x = r.left() + frac * r.width()
                painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        # Conteúdo do balão: todas as séries visíveis naquele instante.
        rows = []
        for s in self.visible_series():
            pair = s.value_at(t)
            if pair is None:
                continue
            rows.append((s, pair[0], pair[1]))
        if not rows:
            return
        rows.sort(key=lambda r: r[1] + r[2], reverse=True)
        limit = 14
        clipped = len(rows) - limit
        rows = rows[:limit]

        font = QFont(self.font())
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 0.5))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        header = time.strftime("%H:%M:%S", time.localtime(t))
        lines = [(None, header, "")]
        for s, rx, tx in rows:
            lines.append((s.color, s.label,
                          f"↓ {format_bps(rx)}   ↑ {format_bps(tx)}"))
        if clipped > 0:
            lines.append((None, f"+{clipped} outras…", ""))

        label_w = max(metrics.horizontalAdvance(text) for _c, text, _v in lines)
        value_w = max(metrics.horizontalAdvance(v) for _c, _t, v in lines)
        line_h = metrics.height() + 2
        box_w = label_w + value_w + 34
        box_h = line_h * len(lines) + 8
        x = self._cursor_x + 12
        if x + box_w > self.width() - 4:
            x = self._cursor_x - box_w - 12
        y = max(2.0, min(rect.top(), self.height() - box_h - 4.0))
        box = QRectF(x, y, box_w, box_h)
        painter.setPen(QPen(_TIP_BORDER, 1))
        painter.setBrush(QBrush(_TIP_BG))
        painter.drawRoundedRect(box, 3, 3)
        painter.setBrush(Qt.NoBrush)

        cursor_y = box.top() + 4
        for color, text, value in lines:
            if color is not None:
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRect(QRectF(box.left() + 6, cursor_y + 4, 8, 8))
                painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor("#1c1c1c"))
            painter.drawText(QRectF(box.left() + 20, cursor_y, label_w, line_h),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.setPen(_AXIS_TEXT)
            painter.drawText(QRectF(box.right() - value_w - 8, cursor_y,
                                    value_w, line_h),
                             Qt.AlignRight | Qt.AlignVCenter, value)
            cursor_y += line_h

    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, event) -> None:
        self._cursor_x = event.position().x()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor_x = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, series in self._plots:
            if rect.contains(pos) and len(series) == 1:
                self.seriesClicked.emit(series[0].key)
                break
        super().mousePressEvent(event)


def _cell_values(s: Series) -> str:
    last = s.samples[-1] if s.samples else (0, 0, 0)
    return f"↓{axis_label(last[1])} ↑{axis_label(last[2])}"


def _elapsed_label(seconds: float, span: float) -> str:
    if seconds < 1:
        return "agora"
    if span >= 7200:
        return f"-{seconds / 3600:.1f}h"
    if span >= 120:
        return f"-{seconds / 60:.0f}min"
    return f"-{seconds:.0f}s"


class MiniChart(QWidget):
    """Sparkline com valor atual — usada nos cartões do Painel."""

    def __init__(self, label: str, unit: str = "%", parent=None,
                 color: str = "#1f6fb2", maximum: float | None = 100.0):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self._label = label
        self._unit = unit
        self._color = QColor(color)
        self._max = maximum
        self._samples: list[tuple[float, float, float]] = []
        self._text = "—"

    def set_samples(self, samples, current_text: str = "") -> None:
        self._samples = list(samples)
        if current_text:
            self._text = current_text
        elif self._samples:
            self._text = f"{self._samples[-1][1]:.0f}{self._unit}"
        else:
            self._text = "—"
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, _PANEL_BG)
        painter.setPen(QPen(QColor("#d5d9de"), 1))
        painter.drawRoundedRect(rect, 3, 3)

        font = QFont(self.font())
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(_AXIS_TEXT)
        painter.drawText(QRectF(rect.left() + 7, rect.top() + 3,
                                rect.width() - 14, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, self._label)

        big = QFont(self.font())
        big.setBold(True)
        big.setPointSizeF(self.font().pointSizeF() + 3)
        painter.setFont(big)
        painter.setPen(self._color.darker(120))
        painter.drawText(QRectF(rect.left() + 7, rect.top() + 16,
                                rect.width() - 14, 20),
                         Qt.AlignLeft | Qt.AlignVCenter, self._text)

        plot = QRectF(rect.left() + 5, rect.top() + 38,
                      rect.width() - 10, rect.height() - 43)
        if len(self._samples) < 2 or plot.height() < 6:
            painter.end()
            return
        values = [s[1] for s in self._samples]
        ceiling = self._max or _nice_ceiling(max(values))
        ceiling = max(ceiling, 1e-9)
        t0, t1 = self._samples[0][0], self._samples[-1][0]
        span = max(0.001, t1 - t0)
        poly = QPolygonF()
        for t, value, _b in self._samples:
            x = plot.left() + (t - t0) / span * plot.width()
            y = plot.bottom() - min(value, ceiling) / ceiling * plot.height()
            poly.append(QPointF(x, y))
        area = QPolygonF(poly)
        area.append(QPointF(poly.last().x(), plot.bottom()))
        area.append(QPointF(poly.first().x(), plot.bottom()))
        grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top_color = QColor(self._color)
        top_color.setAlpha(80)
        grad.setColorAt(0.0, top_color)
        tail = QColor(self._color)
        tail.setAlpha(12)
        grad.setColorAt(1.0, tail)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPolygon(area)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._color, 1.5))
        painter.drawPolyline(poly)
        painter.end()
