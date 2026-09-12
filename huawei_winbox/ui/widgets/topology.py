"""Diagrama de vizinhança LLDP desenhado com QPainter.

Uma tabela de vizinhos diz *quem* está conectado; um desenho diz **como**
a caixa está no meio da rede — e é isso que se procura quando se abre o
equipamento pela primeira vez ou quando alguém pergunta "esse switch está
pendurado onde?".

O equipamento local fica no centro e os vizinhos em volta, em círculo. A
espessura de cada ligação acompanha o tráfego da porta local, então o
uplink real salta aos olhos no meio dos links de acesso. Ligações cuja
porta está fora do ar aparecem tracejadas e em cinza.

Sem dependência de biblioteca de grafos: para a dezena de vizinhos que um
equipamento tem, um arranjo radial é mais legível (e infinitamente mais
barato) que um algoritmo de força.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QFontMetrics, QLinearGradient,
)
from PySide6.QtWidgets import QWidget

from ...core.parsers import format_bps

_BG = QColor("#ffffff")
_CENTER_FILL = QColor("#1f3a5f")
_CENTER_TEXT = QColor("#ffffff")
_NODE_FILL = QColor("#f7f9fb")
_NODE_BORDER = QColor("#9fb0c2")
_NODE_TEXT = QColor("#1c2a36")
_LINK = QColor("#5b7ea6")
_LINK_DOWN = QColor("#b9b9b9")
_LABEL = QColor("#5a6a7a")
_HIGHLIGHT = QColor("#c0392b")

_MIN_LINK_WIDTH = 1.2
_MAX_LINK_WIDTH = 6.0
# Largura máxima de uma caixa: um nome de equipamento comprido não pode
# esticar o desenho inteiro — é abreviado no meio.
_MAX_NODE_WIDTH = 210
# Folga entre caixas vizinhas e entre elas e a central.
_NODE_GAP = 26
_CENTER_GAP = 46


class Neighbor:
    """Um vizinho e a ligação até ele."""

    __slots__ = ("device", "management_ip", "links")

    def __init__(self, device: str, management_ip: str = ""):
        self.device = device or "(sem nome)"
        self.management_ip = management_ip
        # [(porta_local, porta_remota, rx_bps, tx_bps, no_ar)]
        self.links: list[tuple] = []

    @property
    def traffic(self) -> float:
        return max((rx + tx for _l, _r, rx, tx, _up in self.links), default=0.0)

    @property
    def up(self) -> bool:
        return any(link[4] for link in self.links)


class TopologyWidget(QWidget):
    """Desenha o equipamento local cercado pelos seus vizinhos."""

    portClicked = Signal(str)          # porta local sob o clique

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self._center_name = ""
        self._center_detail = ""
        self._neighbors: list[Neighbor] = []
        self._hover: str = ""
        self._hit: list[tuple[QRectF, str]] = []

    # ------------------------------------------------------------------ #
    def set_center(self, name: str, detail: str = "") -> None:
        self._center_name = name
        self._center_detail = detail
        self.update()

    def set_neighbors(self, neighbors: list[Neighbor]) -> None:
        self._neighbors = list(neighbors)
        # O desenho pede o espaço de que precisa em vez de espremer as
        # caixas umas sobre as outras: com o raio fixo anterior, três
        # vizinhos já se sobrepunham à caixa central e os rótulos das
        # portas sumiam atrás delas.
        needed = self._required_size()
        self.setMinimumSize(needed[0], needed[1])
        self.updateGeometry()
        self.update()

    # ------------------------------------------------------------------ #
    def _node_lines(self, neighbor: Neighbor) -> list[str]:
        lines = [neighbor.device]
        if neighbor.management_ip:
            lines.append(neighbor.management_ip)
        remote = neighbor.links[0][1] if neighbor.links else ""
        if remote:
            lines.append(remote)
        if neighbor.traffic > 0:
            rx = sum(link[2] for link in neighbor.links)
            tx = sum(link[3] for link in neighbor.links)
            lines.append(f"↓{format_bps(rx)}  ↑{format_bps(tx)}")
        return lines

    def _fonts(self):
        small = QFont(self.font())
        small.setPointSizeF(max(6.5, self.font().pointSizeF() - 1.5))
        bold = QFont(small)
        bold.setBold(True)
        return small, bold

    def _node_size(self, lines: list[str]) -> tuple[float, float]:
        small, bold = self._fonts()
        metrics, bold_metrics = QFontMetrics(small), QFontMetrics(bold)
        width = max([bold_metrics.horizontalAdvance(lines[0])]
                    + [metrics.horizontalAdvance(t) for t in lines[1:]]) + 20
        height = (metrics.height() + 1) * len(lines) + 10
        return min(float(width), float(_MAX_NODE_WIDTH)), float(height)

    def _required_size(self) -> tuple[int, int]:
        """Tamanho mínimo em que o arranjo radial cabe sem sobreposição."""
        count = len(self._neighbors)
        if not count:
            return 320, 240
        sizes = [self._node_size(self._node_lines(n)) for n in self._neighbors]
        node_w = max(size[0] for size in sizes)
        node_h = max(size[1] for size in sizes)
        center_w, center_h = self._node_size(self._center_lines())

        # 1) as caixas vizinhas não podem se tocar no círculo…
        if count > 1:
            step = math.pi / count
            radius = max((node_w + _NODE_GAP) / (2 * math.sin(step)),
                         (node_h + _NODE_GAP) / (2 * math.sin(step)))
        else:
            radius = 0.0
        # 2) …nem encostar na caixa central.
        radius = max(radius,
                     center_h / 2 + node_h / 2 + _CENTER_GAP,
                     center_w / 2 + node_w / 2 + _CENTER_GAP * 0.4)
        width = int(2 * (radius + node_w / 2) + 24)
        height = int(2 * (radius + node_h / 2) + 24)
        return max(320, width), max(240, height)

    def _center_lines(self) -> list[str]:
        lines = [self._center_name or "este equipamento"]
        if self._center_detail:
            lines.append(self._center_detail)
        lines.append(f"{len(self._neighbors)} vizinho(s)")
        return lines

    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), _BG)
        self._hit = []

        if not self._neighbors:
            painter.setPen(_LABEL)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Nenhum vizinho LLDP visível.\n"
                             "O LLDP precisa estar habilitado dos dois lados.")
            painter.end()
            return

        small, _bold = self._fonts()
        metrics = QFontMetrics(small)

        width, height = self.width(), self.height()
        center = QPointF(width / 2, height / 2)
        count = len(self._neighbors)
        # Raio calculado para caber sem sobreposição; se a janela for maior
        # que o necessário, o desenho se espalha até preenchê-la.
        needed_w, needed_h = self._required_size()
        sizes = [self._node_size(self._node_lines(n)) for n in self._neighbors]
        node_w = max(size[0] for size in sizes)
        node_h = max(size[1] for size in sizes)
        radius = min((needed_w - 24) / 2 - node_w / 2 + max(
                         0, (width - needed_w) / 2),
                     (needed_h - 24) / 2 - node_h / 2 + max(
                         0, (height - needed_h) / 2))
        radius = max(radius, 60.0)

        peak = max((n.traffic for n in self._neighbors), default=0.0)

        # ── Ligações primeiro, para ficarem atrás das caixas ─────────── #
        positions: list[tuple[Neighbor, QPointF]] = []
        for index, neighbor in enumerate(self._neighbors):
            # Começa no topo e gira: previsível e estável entre atualizações.
            angle = -math.pi / 2 + 2 * math.pi * index / count
            point = QPointF(center.x() + radius * math.cos(angle),
                            center.y() + radius * math.sin(angle))
            positions.append((neighbor, point))

            width_px = _MIN_LINK_WIDTH
            if peak > 0 and neighbor.traffic > 0:
                width_px += (_MAX_LINK_WIDTH - _MIN_LINK_WIDTH) * \
                            (neighbor.traffic / peak)
            highlighted = any(link[0] == self._hover for link in neighbor.links)
            if not neighbor.up:
                pen = QPen(_LINK_DOWN, _MIN_LINK_WIDTH, Qt.DashLine)
            else:
                pen = QPen(_HIGHLIGHT if highlighted else _LINK, width_px)
            painter.setPen(pen)
            painter.drawLine(center, point)

            # Rótulo da porta local, junto ao centro.
            painter.setFont(small)
            label = neighbor.links[0][0] if neighbor.links else ""
            if len(neighbor.links) > 1:
                label += f"  (+{len(neighbor.links) - 1})"
            if label:
                mid = QPointF(center.x() + (point.x() - center.x()) * 0.34,
                              center.y() + (point.y() - center.y()) * 0.34)
                text_width = metrics.horizontalAdvance(label) + 8
                box = QRectF(mid.x() - text_width / 2, mid.y() - 8,
                             text_width, 15)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 225))
                painter.drawRoundedRect(box, 3, 3)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(_LABEL)
                painter.drawText(box, Qt.AlignCenter, label)
                if neighbor.links:
                    self._hit.append((box, neighbor.links[0][0]))

        # ── Caixas dos vizinhos ──────────────────────────────────────── #
        for neighbor, point in positions:
            box = self._draw_node(painter, point, self._node_lines(neighbor),
                                  small, metrics, center=False,
                                  up=neighbor.up)
            if neighbor.links:
                self._hit.append((box, neighbor.links[0][0]))

        # ── Caixa central ────────────────────────────────────────────── #
        self._draw_node(painter, center, self._center_lines(), small, metrics,
                        center=True, up=True)
        painter.end()

    # ------------------------------------------------------------------ #
    def _draw_node(self, painter: QPainter, point: QPointF, lines: list[str],
                   font: QFont, metrics: QFontMetrics, center: bool,
                   up: bool) -> QRectF:
        bold = QFont(font)
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        line_height = metrics.height() + 1
        width, height = self._node_size(lines)
        box = QRectF(point.x() - width / 2, point.y() - height / 2,
                     width, height)

        if center:
            gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
            gradient.setColorAt(0.0, QColor("#2f5fa0"))
            gradient.setColorAt(1.0, _CENTER_FILL)
            painter.setBrush(QBrush(gradient))
            painter.setPen(QPen(QColor("#16283f"), 1))
        else:
            painter.setBrush(QBrush(_NODE_FILL))
            painter.setPen(QPen(_NODE_BORDER if up else _LINK_DOWN, 1))
        painter.drawRoundedRect(box, 4, 4)

        cursor = box.top() + 5
        available = int(box.width() - 12)
        for index, text in enumerate(lines):
            active = bold if index == 0 else font
            painter.setFont(active)
            if center:
                painter.setPen(_CENTER_TEXT)
            else:
                painter.setPen(_NODE_TEXT if index == 0 else _LABEL)
            # Nome comprido é abreviado no meio: o começo e o fim de um
            # hostname são o que identifica o equipamento.
            shown = (bold_metrics if index == 0 else metrics).elidedText(
                text, Qt.ElideMiddle, available)
            painter.drawText(QRectF(box.left(), cursor, box.width(),
                                    line_height),
                             Qt.AlignHCenter | Qt.AlignVCenter, shown)
            cursor += line_height
        return box

    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        found = ""
        for box, port in self._hit:
            if box.contains(position):
                found = port
                break
        if found != self._hover:
            self._hover = found
            self.setCursor(Qt.PointingHandCursor if found else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover:
            self._hover = ""
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        position = event.position()
        for box, port in self._hit:
            if box.contains(position):
                self.portClicked.emit(port)
                break
        super().mousePressEvent(event)
