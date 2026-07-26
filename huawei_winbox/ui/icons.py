"""Ícones desenhados em tempo de execução (sem arquivos de imagem).

WinBox usa ícones coloridos no menu lateral e botões com símbolos (+, −,
✓, ✗, funil de filtro...). Para manter o aplicativo autossuficiente — sem
empacotar PNGs nem depender de tema do sistema — os ícones são desenhados
com QPainter aqui e devolvidos como ``QIcon``. Visual de linha fina,
coerente e legível no tema claro.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QPen, QColor, QPolygonF, QBrush,
)

_SIZE = 18

# Paleta de acentos (tom WinBox)
BLUE = "#2f5fa0"
SLATE = "#46586d"
GREEN = "#2e8b57"
RED = "#c0473f"
ORANGE = "#c2801d"
TEAL = "#277f8e"
INDIGO = "#4a4fa0"
PURPLE = "#6b4a9a"


def _pen(p: QPainter, color: str, w: float = 1.5) -> None:
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _poly(points) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


# ── Desenhos de categoria ────────────────────────────────────────────── #
def _ethernet(p, c):
    _pen(p, c, 1.4)
    p.drawRoundedRect(QRectF(3, 5, 12, 8), 1.6, 1.6)
    for x in (5.5, 8, 10.5):
        p.drawLine(QPointF(x, 6.5), QPointF(x, 8.5))
    p.drawRect(QRectF(7.6, 12, 2.8, 1.6))


def _chart(p, c):
    _pen(p, GREEN, 1.6)
    p.drawPolyline(_poly([(3, 13), (6.5, 8.5), (9, 10.5), (12, 5), (15, 8)]))
    _pen(p, c, 1.0)
    p.drawLine(QPointF(3, 14.5), QPointF(15, 14.5))


def _tag(p, c):
    _pen(p, c, 1.4)
    p.drawPolygon(_poly([(3, 4), (10, 4), (15, 9), (10, 14), (3, 14)]))
    p.setBrush(QBrush(QColor(c)))
    p.drawEllipse(QPointF(6, 9), 1.2, 1.2)


def _layers(p, c):
    _pen(p, c, 1.4)
    for dy in (0, 3.2):
        p.drawPolygon(_poly([(9, 2 + dy), (15, 5 + dy),
                             (9, 8 + dy), (3, 5 + dy)]))
    p.drawPolyline(_poly([(3, 11.2), (9, 14.2), (15, 11.2)]))


def _grid(p, c):
    _pen(p, c, 1.3)
    p.drawRoundedRect(QRectF(3, 3, 12, 12), 1.4, 1.4)
    p.drawLine(QPointF(7, 3), QPointF(7, 15))
    p.drawLine(QPointF(11, 3), QPointF(11, 15))
    p.drawLine(QPointF(3, 7), QPointF(15, 7))
    p.drawLine(QPointF(3, 11), QPointF(15, 11))


def _route(p, c):
    _pen(p, c, 1.5)
    p.drawLine(QPointF(5.5, 9), QPointF(12.5, 4.5))
    p.drawLine(QPointF(5.5, 9), QPointF(12.5, 13.5))
    p.setBrush(QBrush(QColor(c)))
    for cx, cy in ((4.2, 9), (13.6, 4.2), (13.6, 13.8)):
        p.drawEllipse(QPointF(cx, cy), 1.7, 1.7)


def _swap(p, c):
    _pen(p, c, 1.4)
    p.drawLine(QPointF(4, 7), QPointF(13, 7))
    p.drawPolyline(_poly([(11, 5), (13.5, 7), (11, 9)]))
    p.drawLine(QPointF(14, 11), QPointF(5, 11))
    p.drawPolyline(_poly([(7, 9), (4.5, 11), (7, 13)]))


def _shield(p, c):
    _pen(p, c, 1.4)
    p.drawPolygon(_poly([(9, 2.5), (15, 5), (15, 9.5),
                        (9, 15.5), (3, 9.5), (3, 5)]))


def _lock(p, c):
    _pen(p, c, 1.4)
    p.drawArc(QRectF(5.5, 3, 7, 8), 0, 180 * 16)
    p.drawRoundedRect(QRectF(4.5, 8, 9, 7), 1.4, 1.4)
    p.setBrush(QBrush(QColor(c)))
    p.drawEllipse(QPointF(9, 11), 1.0, 1.0)


def _gear(p, c):
    _pen(p, c, 1.4)
    p.drawEllipse(QPointF(9, 9), 3.4, 3.4)
    p.drawEllipse(QPointF(9, 9), 1.3, 1.3)
    import math
    for k in range(8):
        a = math.radians(k * 45)
        p.drawLine(QPointF(9 + 3.6 * math.cos(a), 9 + 3.6 * math.sin(a)),
                   QPointF(9 + 5.2 * math.cos(a), 9 + 5.2 * math.sin(a)))


def _sliders(p, c):
    _pen(p, c, 1.4)
    for y, kx in ((6, 11), (12, 6)):
        p.drawLine(QPointF(3, y), QPointF(15, y))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(kx, y), 1.8, 1.8)


def _user(p, c):
    _pen(p, c, 1.4)
    p.drawEllipse(QPointF(9, 6), 2.6, 2.6)
    p.drawArc(QRectF(3.5, 9.5, 11, 10), 20 * 16, 140 * 16)


def _antenna(p, c):
    _pen(p, c, 1.4)
    p.drawLine(QPointF(9, 15), QPointF(9, 7))
    p.drawLine(QPointF(7, 15.5), QPointF(11, 15.5))
    p.setBrush(QBrush(QColor(c)))
    p.drawEllipse(QPointF(9, 6), 1.4, 1.4)
    _pen(p, c, 1.2)
    p.drawArc(QRectF(4.5, 2, 9, 9), 40 * 16, 100 * 16)
    p.drawArc(QRectF(6, 3.5, 6, 6), 40 * 16, 100 * 16)


def _doc(p, c):
    _pen(p, c, 1.4)
    p.drawPolygon(_poly([(4, 2.5), (11, 2.5), (14, 5.5),
                        (14, 15.5), (4, 15.5)]))
    p.drawPolyline(_poly([(11, 2.5), (11, 5.5), (14, 5.5)]))
    _pen(p, c, 1.1)
    for y in (8.5, 10.8, 13):
        p.drawLine(QPointF(6, y), QPointF(12, y))


def _chip(p, c):
    _pen(p, c, 1.4)
    p.drawRoundedRect(QRectF(5, 5, 8, 8), 1.0, 1.0)
    for x in (7.5, 10.5):
        p.drawLine(QPointF(x, 2.5), QPointF(x, 5))
        p.drawLine(QPointF(x, 13), QPointF(x, 15.5))
    for y in (7.5, 10.5):
        p.drawLine(QPointF(2.5, y), QPointF(5, y))
        p.drawLine(QPointF(13, y), QPointF(15.5, y))


def _wrench(p, c):
    _pen(p, c, 1.7)
    p.drawLine(QPointF(6, 12), QPointF(11.5, 6.5))
    _pen(p, c, 1.4)
    p.drawArc(QRectF(9.5, 3, 6, 6), 120 * 16, 220 * 16)
    p.setBrush(QBrush(QColor(c)))
    p.drawEllipse(QPointF(6, 12.5), 1.6, 1.6)


def _terminal(p, c):
    _pen(p, c, 1.4)
    p.drawRoundedRect(QRectF(3, 4, 12, 10), 1.6, 1.6)
    _pen(p, c, 1.3)
    p.drawPolyline(_poly([(5.5, 8), (7.5, 9.5), (5.5, 11)]))
    p.drawLine(QPointF(8.5, 11), QPointF(11.5, 11))


# ── Desenhos de ações (toolbar) ──────────────────────────────────────── #
def _add(p, c):
    _pen(p, GREEN, 2.0)
    p.drawLine(QPointF(9, 4), QPointF(9, 14))
    p.drawLine(QPointF(4, 9), QPointF(14, 9))


def _remove(p, c):
    _pen(p, RED, 2.0)
    p.drawLine(QPointF(4, 9), QPointF(14, 9))


def _enable(p, c):
    _pen(p, GREEN, 2.0)
    p.drawPolyline(_poly([(4, 9.5), (7.5, 13), (14, 5)]))


def _disable(p, c):
    _pen(p, RED, 2.0)
    p.drawLine(QPointF(5, 5), QPointF(13, 13))
    p.drawLine(QPointF(13, 5), QPointF(5, 13))


def _comment(p, c):
    _pen(p, ORANGE, 1.5)
    p.drawRoundedRect(QRectF(3, 4, 12, 8), 2, 2)
    p.drawPolyline(_poly([(6, 12), (6, 14.5), (9, 12)]))


def _filter(p, c):
    _pen(p, BLUE, 1.6)
    p.drawPolyline(_poly([(3.5, 4), (14.5, 4), (10.5, 9), (10.5, 14),
                         (7.5, 12), (7.5, 9), (3.5, 4)]))


def _refresh(p, c):
    _pen(p, BLUE, 1.6)
    p.drawArc(QRectF(4, 4, 10, 10), 60 * 16, 250 * 16)
    p.setBrush(QBrush(QColor(BLUE)))
    p.drawPolygon(_poly([(12.5, 3.5), (14.5, 6), (11, 6.5)]))


def _find(p, c):
    _pen(p, SLATE, 1.6)
    p.drawEllipse(QPointF(8, 8), 3.6, 3.6)
    p.drawLine(QPointF(11, 11), QPointF(14.5, 14.5))


def _save(p, c):
    _pen(p, BLUE, 1.4)
    p.drawPolygon(_poly([(4, 3.5), (12.5, 3.5), (14.5, 5.5),
                        (14.5, 14.5), (4, 14.5)]))
    p.drawRect(QRectF(6.5, 3.5, 5, 3.5))
    p.drawRect(QRectF(6, 9.5, 6, 5))


_DRAW = {
    "ethernet": _ethernet, "chart": _chart, "tag": _tag, "layers": _layers,
    "grid": _grid, "route": _route, "swap": _swap, "shield": _shield,
    "lock": _lock, "gear": _gear, "sliders": _sliders, "user": _user,
    "antenna": _antenna, "doc": _doc, "chip": _chip, "wrench": _wrench,
    "terminal": _terminal,
    # ações
    "add": _add, "remove": _remove, "enable": _enable, "disable": _disable,
    "comment": _comment, "filter": _filter, "refresh": _refresh,
    "find": _find, "save": _save,
}

# Cada item do menu lateral -> (desenho, cor)
_MENU_ICON = {
    "Painel": ("grid", BLUE),
    "Interfaces": ("ethernet", BLUE),
    "Tráfego (gráfico)": ("chart", GREEN),
    "Configuração": ("doc", INDIGO),
    "Endereços IP": ("tag", TEAL),
    "VLANs": ("layers", ORANGE),
    "Tabela MAC": ("grid", PURPLE),
    "Rotas": ("route", BLUE),
    "ARP": ("swap", TEAL),
    "BGP": ("route", INDIGO),
    "OSPF": ("route", INDIGO),
    "IPv6": ("tag", TEAL),
    "MPLS / LDP": ("tag", ORANGE),
    "STP / MSTP": ("layers", GREEN),
    "VRRP": ("shield", RED),
    "QoS": ("sliders", ORANGE),
    "Segurança L2": ("lock", RED),
    "Servidor DHCP": ("gear", BLUE),
    "PPPoE / BAS": ("user", INDIGO),
    "NAT": ("swap", TEAL),
    "ACL / Firewall": ("shield", RED),
    "Firewall / NAT": ("filter", RED),
    "Usuários": ("user", SLATE),
    "Vizinhos (LLDP)": ("route", INDIGO),
    "Serviços": ("gear", SLATE),
    "SNMP": ("antenna", GREEN),
    "Alarmes": ("shield", RED),
    "Logs": ("doc", SLATE),
    "Hardware": ("chip", SLATE),
    "Ferramentas": ("wrench", SLATE),
    "Sistema": ("gear", SLATE),
    "Novo Terminal": ("terminal", "#1c2a36"),
}

_cache: dict[tuple, QIcon] = {}


def _make(name: str, color: str) -> QIcon:
    key = (name, color)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    draw = _DRAW.get(name)
    if draw:
        draw(p, color)
    p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon


def menu_icon(label: str) -> QIcon:
    """Ícone para um item do menu lateral (pelo rótulo)."""
    name, color = _MENU_ICON.get(label, ("doc", SLATE))
    return _make(name, color)


def action_icon(name: str, color: str = SLATE) -> QIcon:
    """Ícone para um botão de ação da toolbar (add, remove, filter...)."""
    return _make(name, color)
