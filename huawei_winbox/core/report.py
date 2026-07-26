"""Relatório do equipamento em HTML — documentação num clique.

Depois de mexer num equipamento, alguém sempre precisa do retrato: o que
está configurado, como estão as portas, quem são os vizinhos, o que está
em alarme. Montar isso à mão é copiar dez saídas de comando para um
documento.

Este módulo recebe os dados já coletados e devolve **um arquivo HTML
autocontido** — sem CSS externo, sem imagem, sem fonte remota — que abre
em qualquer navegador, imprime bem e pode ser anexado num chamado ou
guardado como registro da manutenção.

O módulo é puro: nenhuma consulta ao equipamento acontece aqui, o que
permite testá-lo com dados fabricados.
"""

from __future__ import annotations

import html
import time

from .parsers import format_bps

# Paleta alinhada com a da interface, para o relatório não parecer de
# outro programa.
_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px;background:#f4f6f8;color:#1c1c1c;
     font:13px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
h1{margin:0 0 2px;font-size:21px;color:#1f3a5f}
h2{margin:26px 0 8px;font-size:15px;color:#1f3a5f;
   border-bottom:2px solid #d9e2ec;padding-bottom:4px}
.sub{color:#5a6a7a;margin:0 0 18px;font-size:12px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 4px}
.card{flex:1 1 150px;background:#fff;border:1px solid #d9e2ec;border-radius:5px;
      padding:10px 12px}
.card .k{font-size:11px;color:#5a6a7a;text-transform:uppercase;
         letter-spacing:.4px}
.card .v{font-size:19px;font-weight:600;margin-top:2px}
/* Valores longos (uptime, banda total) quebravam o cartão em três linhas
   e desalinhavam a fileira inteira; abaixam de corpo em vez de embolar. */
.card .v.long{font-size:14px;line-height:1.35}
table{width:100%;border-collapse:collapse;background:#fff;
      border:1px solid #d9e2ec;border-radius:5px;overflow:hidden}
th{background:#eef2f6;text-align:left;font-size:11px;text-transform:uppercase;
   letter-spacing:.4px;color:#42536b;padding:7px 9px;
   border-bottom:1px solid #d9e2ec}
td{padding:6px 9px;border-bottom:1px solid #eef2f6;vertical-align:top}
tr:last-child td{border-bottom:none}
tr:nth-child(even) td{background:#fbfcfd}
.mono{font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.crit{color:#96281b;font-weight:600}
.warn{color:#8a6100;font-weight:600}
.ok{color:#1e7a45}
.mute{color:#7a8797}
.bar{position:relative;background:#e8ebee;border-radius:3px;height:14px;
     min-width:70px}
.bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:3px;
          background:#2e8b57}
.bar.hi>span{background:#c0392b}
.bar.mid>span{background:#d08a1f}
pre{background:#fff;border:1px solid #d9e2ec;border-radius:5px;padding:12px;
    overflow-x:auto;font-family:Consolas,"DejaVu Sans Mono",monospace;
    font-size:11.5px;line-height:1.45;white-space:pre;margin:0}
footer{margin-top:28px;color:#7a8797;font-size:11px;
       border-top:1px solid #d9e2ec;padding-top:10px}
@media print{body{background:#fff;padding:0}
             table,.card,pre{border-color:#bbb}
             h2{page-break-after:avoid}}
"""


def _e(value) -> str:
    """Escapa para HTML — os dados vêm do equipamento, não de nós."""
    return html.escape("" if value is None else str(value), quote=True)


def _card(title: str, value: str, tone: str = "") -> str:
    classes = ["v"]
    if tone:
        classes.append(tone)
    if len(str(value)) > 16:
        classes.append("long")
    return (f'<div class="card"><div class="k">{_e(title)}</div>'
            f'<div class="{" ".join(classes)}">{_e(value)}</div></div>')


def _bar(percent: float | None) -> str:
    if percent is None:
        return '<span class="mute">—</span>'
    percent = max(0.0, min(100.0, float(percent)))
    tone = "hi" if percent >= 85 else ("mid" if percent >= 60 else "")
    return (f'<div class="bar {tone}" title="{percent:.1f}%">'
            f'<span style="width:{percent:.1f}%"></span></div>')


def _table(headers: list[str], rows: list[list[str]],
           empty: str = "sem registros") -> str:
    if not rows:
        return f'<p class="mute">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _status_class(status: str) -> str:
    return {"crítico": "crit", "atenção": "warn", "ok": "ok"}.get(status, "mute")


def build(data: dict) -> str:
    """Monta o HTML do relatório a partir dos dados coletados.

    ``data`` aceita as chaves ``device``, ``health``, ``interfaces``,
    ``optics``, ``alarms``, ``neighbors``, ``bgp``, ``ospf``,
    ``rates`` e ``config``. O que faltar é simplesmente omitido — um
    relatório de switch de acesso não precisa fingir que tem BGP.
    """
    device = data.get("device") or {}
    health = data.get("health") or {}
    generated = time.strftime("%d/%m/%Y às %H:%M:%S")
    parts: list[str] = []

    hostname = device.get("hostname") or "equipamento"
    parts.append(f"<h1>{_e(hostname)}</h1>")
    subtitle = " · ".join(filter(None, [
        device.get("host", ""),
        device.get("product", ""),
        device.get("software", ""),
    ]))
    parts.append(f'<p class="sub">{_e(subtitle)}<br>'
                 f'Relatório gerado em {_e(generated)} pelo HuaweiBox</p>')

    # ── Cartões de resumo ── #
    cards = []
    if device.get("uptime"):
        cards.append(_card("Ligado há", device["uptime"]))
    for key, label in (("cpu", "CPU"), ("memory", "Memória")):
        value = health.get(key)
        cards.append(_card(label, f"{value:.0f}%" if value is not None else "—",
                           "crit" if (value or 0) >= 85 else
                           ("warn" if (value or 0) >= 60 else "")))
    if health.get("temperature") is not None:
        cards.append(_card("Temperatura", f"{health['temperature']:.0f} °C"))
    interfaces = data.get("interfaces") or []
    if interfaces:
        up = sum(1 for row in interfaces
                 if "up" in str(row.get("phy", "")).lower())
        cards.append(_card("Interfaces ativas", f"{up} de {len(interfaces)}"))
    alarms = data.get("alarms") or []
    critical = sum(1 for alarm in alarms if alarm.get("level", 9) <= 2)
    cards.append(_card("Alarmes ativos", str(len(alarms)),
                       "crit" if critical else ""))
    if data.get("totals"):
        rx, tx = data["totals"]
        cards.append(_card("Banda total", f"↓ {rx}  ↑ {tx}"))
    parts.append(f'<div class="cards">{"".join(cards)}</div>')

    # ── Alarmes ── #
    if alarms:
        parts.append("<h2>Alarmes ativos</h2>")
        rows = [[
            f'<span class="{_status_class(a.get("severity", ""))}">'
            f'{_e(a.get("severity", ""))}</span>',
            _e(a.get("stamp", "")),
            f'<span class="mono">{_e(a.get("id", ""))}</span>',
            _e(a.get("description", "")),
        ] for a in alarms]
        parts.append(_table(["Severidade", "Desde", "Identificador",
                             "Descrição"], rows))

    # ── Saúde óptica ── #
    optics = data.get("optics") or {}
    if optics:
        parts.append("<h2>Diagnóstico óptico</h2>")
        ordered = sorted(optics.values(),
                         key=lambda e: ({"crítico": 0, "atenção": 1}.get(
                             e.get("status", ""), 2), e.get("name", "")))
        rows = []
        for entry in ordered:
            margin = entry.get("margin_db")
            rows.append([
                f'<span class="mono">{_e(entry.get("name"))}</span>',
                f'<span class="{_status_class(entry.get("status", ""))}">'
                f'{_e(entry.get("status") or "—")}</span>',
                f'<span class="num">{_fmt(entry.get("rx_power"))}</span>',
                f'<span class="num">'
                f'{("%+.1f dB" % margin) if margin is not None else "—"}</span>',
                f'<span class="num">{_fmt(entry.get("tx_power"))}</span>',
                _e(entry.get("type") or "—"),
                _e(entry.get("detail") or ""),
            ])
        parts.append(_table(
            ["Porta", "Situação", "RX (dBm)", "Folga", "TX (dBm)", "Módulo",
             "Observação"], rows))

    # ── Interfaces ── #
    if interfaces:
        parts.append("<h2>Interfaces</h2>")
        rates = data.get("rates") or {}
        speeds = data.get("speeds") or {}
        rows = []
        for row in interfaces:
            name = row.get("name", "")
            phy = str(row.get("phy", ""))
            is_up = "up" in phy.lower() and not phy.startswith("*")
            pair = rates.get(name)
            speed = speeds.get(name) or 0
            usage = None
            if pair and speed:
                usage = max(pair[0], pair[1]) / speed * 100.0
            rows.append([
                f'<span class="mono">{_e(name)}</span>',
                f'<span class="{"ok" if is_up else "mute"}">{_e(phy)}</span>',
                _e(row.get("protocol", "")),
                f'<span class="num">{_e(format_bps(pair[0]) if pair else "—")}</span>',
                f'<span class="num">{_e(format_bps(pair[1]) if pair else "—")}</span>',
                _bar(usage),
                _e(row.get("description", "")),
            ])
        parts.append(_table(
            ["Interface", "Físico", "Protocolo", "RX", "TX", "Uso",
             "Descrição"], rows))

    # ── Vizinhos ── #
    neighbors = data.get("neighbors") or []
    if neighbors:
        parts.append("<h2>Vizinhos (LLDP)</h2>")
        rows = [[
            f'<span class="mono">{_e(n.get("local"))}</span>',
            _e(n.get("neighbor_dev")),
            f'<span class="mono">{_e(n.get("neighbor_intf"))}</span>',
        ] for n in neighbors]
        parts.append(_table(["Porta local", "Equipamento vizinho",
                             "Porta remota"], rows))

    # ── Roteamento ── #
    bgp = data.get("bgp") or []
    if bgp:
        parts.append("<h2>Vizinhos BGP</h2>")
        rows = []
        for peer in bgp:
            established = peer.get("state", "").lower().startswith("estab")
            rows.append([
                f'<span class="mono">{_e(peer.get("peer"))}</span>',
                _e(peer.get("as")),
                f'<span class="{"ok" if established else "crit"}">'
                f'{_e(peer.get("state"))}</span>',
                _e(peer.get("up_down")),
                f'<span class="num">{_e(peer.get("prefrcv"))}</span>',
            ])
        parts.append(_table(["Vizinho", "AS", "Estado", "Tempo",
                             "Prefixos"], rows))

    ospf = data.get("ospf") or []
    if ospf:
        parts.append("<h2>Vizinhanças OSPF</h2>")
        rows = [[
            _e(n.get("area")),
            f'<span class="mono">{_e(n.get("interface"))}</span>',
            f'<span class="mono">{_e(n.get("neighbor"))}</span>',
            f'<span class="{"ok" if n.get("state","").lower().startswith("full") else "warn"}">'
            f'{_e(n.get("state"))}</span>',
        ] for n in ospf]
        parts.append(_table(["Área", "Interface", "Vizinho", "Estado"], rows))

    # ── Configuração ── #
    config = data.get("config")
    if config:
        lines = config.splitlines()
        parts.append(f"<h2>Configuração em execução "
                     f"<span class='mute'>({len(lines)} linhas)</span></h2>")
        parts.append(f"<pre>{_e(config)}</pre>")

    parts.append(
        '<footer>Gerado pelo HuaweiBox. Os dados refletem o momento da '
        'coleta; nada foi alterado no equipamento para produzir este '
        'relatório.</footer>')

    return (f"<!doctype html><html lang=\"pt-BR\"><head>"
            f"<meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,"
            f"initial-scale=1\">"
            f"<title>{_e(hostname)} — relatório HuaweiBox</title>"
            f"<style>{_CSS}</style></head><body>"
            f"{''.join(parts)}</body></html>")


def _fmt(value, decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"
