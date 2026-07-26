"""Parsers da saída dos comandos ``display`` do VRP.

Convertem o texto retornado pelo equipamento em listas de dicionários
para preencher as tabelas da interface gráfica. São tolerantes a
variações de formato entre versões do VRP — linhas que não casam com o
padrão esperado são simplesmente ignoradas.
"""

from __future__ import annotations

import re

# Nome de interface genérico: prefixo numérico opcional (40GE, 100GE),
# letras (GE, XGE, GigabitEthernet, Eth-Trunk, Vlanif, MEth, LoopBack...)
# e a numeração da porta (1/0/1, 0/0/1.100, 100, 0:1...). Cobre tanto os
# nomes completos quanto as abreviações que cada comando/modelo usa.
_IFACE_NAME = r"(?:\d{1,3})?[A-Za-z][A-Za-z0-9\-]*\d[\d/.:]*"

# Nome de interface citado no meio de uma frase (mensagem de log, alarme).
# Exige a barra da numeração de porta para não casar com qualquer palavra
# seguida de número ("VLAN 100", "slot 1").
INTERFACE_IN_TEXT = re.compile(
    r"\b(?:\d{1,3})?[A-Za-z][A-Za-z0-9\-]*\d+(?:/\d+)+(?:\.\d+)?\b")


def parse_interface_brief(text: str) -> list[dict]:
    """``display interface brief`` ->
    [{name, phy, protocol, in_util, out_util, in_errors, out_errors}]

    As duas últimas colunas (``inErrors``/``outErrors``) só existem em
    parte das versões; quando faltam, vêm como ``None``. Elas valem muito:
    saem do mesmo comando que a telemetria já executa, então acompanhar
    erros por porta não custa consulta nenhuma a mais.
    """
    rows = []
    pattern = re.compile(
        rf"^({_IFACE_NAME})\s+(\*?[Uu]p|[Dd]own|\*down|\^down)\s+(\S+)"
        r"(?:\s+([\d.]+%|-+)\s+([\d.]+%|-+))?"
        r"(?:\s+(\d+)\s+(\d+))?"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "name": expand_iface_name(m.group(1)),
            "phy": m.group(2),
            "protocol": m.group(3),
            "in_util": m.group(4) or "",
            "out_util": m.group(5) or "",
            "in_errors": int(m.group(6)) if m.group(6) is not None else None,
            "out_errors": int(m.group(7)) if m.group(7) is not None else None,
        })
    return rows


def parse_interface_description(text: str) -> list[dict]:
    """``display interface description`` ->
    [{name, phy, protocol, description}]"""
    rows = []
    in_data = False
    # (?:\(\w+\))? absorve anotações de velocidade: 100GE0/3/0(40G), GE0/3/5(10G)
    pattern = re.compile(
        rf"^({_IFACE_NAME})(?:\(\w+\))?\s+(\S+)\s+(\S+)\s*(.*)?$"
    )
    for line in text.splitlines():
        line_stripped = line.rstrip()
        # Aguarda a linha de cabeçalho para começar a coleta
        if re.search(r"\bPHY\b.*\bProtocol\b", line_stripped, re.I):
            in_data = True
            continue
        if not in_data:
            continue
        m = pattern.match(line_stripped.strip())
        if not m:
            continue
        rows.append({
            "name": expand_iface_name(m.group(1)),
            "phy": m.group(2),
            "protocol": m.group(3),
            "description": (m.group(4) or "").strip(),
        })
    return rows


def format_bps(bps: int) -> str:
    """Formata bits/s de forma legível: 1234567 -> '1.23 Mbps'."""
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} Kbps"
    return f"{bps} bps"


def parse_interface_rates_bps(text: str) -> dict[str, tuple[int, int]]:
    """``display interface`` (uma ou todas) -> {nome: (rx_bps, tx_bps)}.

    Percorre o dump associando cada bloco "X current state" às linhas
    "input rate ... bits/sec" / "output rate ... bits/sec" que o seguem
    ("Last 300 seconds input rate", "300 seconds output rate"...). Retorna
    os valores BRUTOS em bits/s (inteiros) — base numérica do gráfico de
    tráfego. ``parse_interface_rates`` formata isto para exibição.
    """
    rates: dict[str, tuple[int, int]] = {}
    current = ""
    rx = tx = -1
    state_re = re.compile(rf"^({_IFACE_NAME}) current state\s*:", re.I)
    in_re = re.compile(r"input rate[:\s]+([\d,]+)\s*bits/sec", re.I)
    out_re = re.compile(r"output rate[:\s]+([\d,]+)\s*bits/sec", re.I)

    def flush():
        if current and rx >= 0 and tx >= 0:
            rates[expand_iface_name(current)] = (rx, tx)

    for line in text.splitlines():
        s = line.strip()
        m = state_re.match(s)
        if m:
            flush()
            current, rx, tx = m.group(1), -1, -1
            continue
        m = in_re.search(s)
        if m and rx < 0:
            rx = int(m.group(1).replace(",", ""))
            continue
        m = out_re.search(s)
        if m and tx < 0:
            tx = int(m.group(1).replace(",", ""))
    flush()
    return rates


def parse_interface_rates(text: str) -> dict[str, tuple[str, str]]:
    """``display interface`` (todas) -> {nome: (rx, tx)} em Mbps/Gbps."""
    return {name: (format_bps(rx), format_bps(tx))
            for name, (rx, tx) in parse_interface_rates_bps(text).items()}


# Formas em que o VRP anuncia a banda da porta, em ordem de preferência.
_BW_PATTERNS = [
    # "Port BW: 10G, Transceiver max BW: 10G" / "Current BW: 1G"
    (re.compile(r"(?:Port|Current|Transceiver max)\s*BW\s*:\s*(\d+)\s*([GMK])",
                re.I), "suffix"),
    # "Speed : 1000, Loopback: NONE"  (megabits)
    (re.compile(r"\bSpeed\s*:\s*(\d+)", re.I), "mbps"),
    # "The Maximum Transmit Unit is 1500 bytes ... bandwidth 1000000 kbit"
    (re.compile(r"\bbandwidth\s+(\d+)\s*kbit", re.I), "kbps"),
]
_BW_SUFFIX = {"G": 1_000_000_000, "M": 1_000_000, "K": 1_000}


def parse_interface_bandwidth_bps(text: str) -> dict[str, int]:
    """``display interface`` (uma ou todas) -> {nome: banda_em_bps}.

    A velocidade nominal da porta é o que permite converter a coluna de
    utilização (%) do ``display interface brief`` em bits/s. O ``brief`` é
    muito mais barato que o dump completo, então a banda é lida uma vez e
    reaproveitada em todas as amostras seguintes.
    """
    speeds: dict[str, int] = {}
    current = ""
    state_re = re.compile(rf"^({_IFACE_NAME}) current state\s*:", re.I)
    for line in text.splitlines():
        s = line.strip()
        m = state_re.match(s)
        if m:
            current = expand_iface_name(m.group(1))
            continue
        if not current or current in speeds:
            continue
        for pattern, kind in _BW_PATTERNS:
            m = pattern.search(s)
            if not m:
                continue
            if kind == "suffix":
                value = int(m.group(1)) * _BW_SUFFIX.get(
                    m.group(2).upper(), 1_000_000)
            elif kind == "mbps":
                value = int(m.group(1)) * 1_000_000
            else:
                value = int(m.group(1)) * 1_000
            if value > 0:
                speeds[current] = value
            break
    return speeds


def parse_interface_states(text: str) -> dict[str, tuple[str, str]]:
    """``display interface`` (todas) -> {nome: (estado_físico, protocolo)}.

    O dump completo já diz o estado de cada porta nas linhas
    ``X current state : UP`` e ``Line protocol current state : UP``;
    aproveitá-lo evita uma consulta extra só para saber quem está no ar.
    """
    states: dict[str, tuple[str, str]] = {}
    current = ""
    state_re = re.compile(rf"^({_IFACE_NAME}) current state\s*:\s*(.+)$", re.I)
    proto_re = re.compile(r"^Line protocol current state\s*:\s*(.+)$", re.I)
    for line in text.splitlines():
        stripped = line.strip()
        match = state_re.match(stripped)
        if match:
            current = expand_iface_name(match.group(1))
            states[current] = (match.group(2).strip(), "")
            continue
        match = proto_re.match(stripped)
        if match and current:
            states[current] = (states[current][0], match.group(1).strip())
    return states


def parse_interface_utilization(text: str) -> dict[str, tuple[float, float]]:
    """``display interface brief`` -> {nome: (in_util_%, out_util_%)}.

    Só devolve as portas em que o equipamento realmente informou a
    utilização (a coluna vem como "--" quando não há estatística).
    """
    out: dict[str, tuple[float, float]] = {}
    for row in parse_interface_brief(text):
        try:
            in_pct = float(row["in_util"].rstrip("%"))
            out_pct = float(row["out_util"].rstrip("%"))
        except (ValueError, AttributeError):
            continue
        out[row["name"]] = (in_pct, out_pct)
    return out


def parse_interface_detail(text: str) -> dict:
    """``display interface <name>`` ->
    dict com status, velocidade, duplex, potência ótica, estatísticas."""
    info = {
        "state": "", "protocol": "", "description": "",
        "speed": "", "duplex": "", "negotiation": "", "mtu": "",
        "tx_power": "", "rx_power": "", "bias_current": "",
        "temperature": "", "transceiver_type": "",
        "wavelength": "", "vendor": "", "part_number": "",
        "input_rate": "", "output_rate": "",
        "input_errors": "", "output_errors": "", "raw": text,
    }
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r".+current state\s*:\s*(.+)", s, re.I)
        if m and not info["state"]:
            info["state"] = m.group(1).strip()
        m = re.match(r"Line protocol current state\s*:\s*(.+)", s, re.I)
        if m:
            info["protocol"] = m.group(1).strip()
        m = re.match(r"Description\s*[:\-]\s*(.+)", s, re.I)
        if m:
            info["description"] = m.group(1).strip()
        # O VRP põe vários campos na MESMA linha, separados por vírgula:
        #   Speed : 1000,  Loopback: NONE
        #   Duplex: FULL,  Negotiation: ENABLE
        # Com re.match (ancorado no início) só o primeiro campo de cada
        # linha era lido e "Negociação" ficava sempre vazia. O valor também
        # vinha com a vírgula grudada ("FULL,").
        m = re.search(r"\bSpeed\s*:\s*(\d+)", s)
        if m and not info["speed"]:
            info["speed"] = m.group(1)
        m = re.search(r"\bDuplex\s*:\s*([^\s,]+)", s)
        if m and not info["duplex"]:
            info["duplex"] = m.group(1)
        m = re.search(r"\bNegotiation\s*:\s*([^\s,]+)", s)
        if m and not info["negotiation"]:
            info["negotiation"] = m.group(1)
        m = re.search(r"\bMTU\s+(\d+)|Maximum Transmit Unit is\s+(\d+)", s)
        if m and not info["mtu"]:
            info["mtu"] = m.group(1) or m.group(2)
        m = re.search(r"Port TX Power.*?:\s*(-?[\d.]+)", s)
        if m:
            info["tx_power"] = m.group(1) + " dBm"
        m = re.search(r"Port RX Power.*?:\s*(-?[\d.]+)", s)
        if m:
            info["rx_power"] = m.group(1) + " dBm"
        m = re.search(r"Port Bias Current.*?:\s*([\d.]+)", s)
        if m:
            info["bias_current"] = m.group(1) + " mA"
        m = re.search(r"Temperature.*?:\s*(-?[\d.]+)", s)
        if m:
            info["temperature"] = m.group(1) + " °C"
        m = re.match(r"Transceiver Type\s*:\s*(.+)", s)
        if m:
            info["transceiver_type"] = m.group(1).strip()
        m = re.search(r"Input bandwidth utilization\s*:\s*(\S+)", s)
        if m:
            info["input_rate"] = m.group(1)
        m = re.search(r"Output bandwidth utilization\s*:\s*(\S+)", s)
        if m:
            info["output_rate"] = m.group(1)
        m = re.match(r"(\d+) input error", s)
        if m:
            info["input_errors"] = m.group(1)
        m = re.match(r"(\d+) output error", s)
        if m:
            info["output_errors"] = m.group(1)
    return info


# Abreviações usadas em "display interface description" que o comando
# "display transceiver interface" NÃO aceita — precisam do nome completo.
# 40GE/100GE/25GE já são o nome completo do tipo e ficam como estão.
_IFACE_ABBREV = {
    "GE": "GigabitEthernet",
    "XGE": "XGigabitEthernet",
    "FE": "FastEthernet",
    "Eth": "Ethernet",
}


def is_physical_iface(name: str) -> bool:
    """Retorna True apenas para interfaces físicas que podem ter transceptor.

    Sub-interfaces (100GE0/3/0.370), Vlanif, LoopBack, NULL, VE, Eth-Trunk
    não têm SFP — não adianta tentar display transceiver nelas.
    """
    # Sub-interface: tem ponto no número da porta (0/3/0.370)
    if re.search(r"\.\d+$", name):
        return False
    lower = name.lower()
    for prefix in ("vlan", "loop", "null", "ve", "eth-trunk", "meth"):
        if lower.startswith(prefix):
            return False
    return True


def expand_iface_name(name: str) -> str:
    """Expande a abreviação do tipo da interface para o nome completo.

    ``display interface description`` lista as portas abreviadas
    (GE0/0/1, XGE0/0/4), mas comandos como ``interface GE0/0/1`` ou
    ``display interface GE0/0/1`` falham em vários firmwares com
    "Wrong parameter". Exemplos:
    ``GE0/0/1`` → ``GigabitEthernet0/0/1``;
    ``XGE0/0/4`` → ``XGigabitEthernet0/0/4``;
    ``40GE0/0/1`` e nomes já completos ficam como estão.
    """
    m = re.match(r"^(\d{0,3}[A-Za-z][A-Za-z-]*?)(\d[\d/.:]*)$", name)
    if not m:
        return name
    prefix, number = m.group(1), m.group(2)
    return _IFACE_ABBREV.get(prefix, prefix) + number


def transceiver_iface_arg(name: str) -> str:
    """Converte o nome da interface para a forma exigida por
    ``display transceiver interface``.

    O VRP trata tipo e número como tokens separados nesse comando — e não
    aceita as abreviações da tabela de descrição. Exemplos:
    ``XGE0/0/4`` → ``XGigabitEthernet 0/0/4``;
    ``GE0/0/1`` → ``GigabitEthernet 0/0/1``;
    ``40GE0/0/1`` → ``40GE 0/0/1``.
    """
    m = re.match(r"^(\d{0,3}[A-Za-z][A-Za-z-]*?)(\d[\d/.:]*)$", name)
    if not m:
        return name
    prefix, number = m.group(1), m.group(2)
    prefix = _IFACE_ABBREV.get(prefix, prefix)
    return f"{prefix} {number}"


def merge_transceiver_detail(info: dict, text: str) -> dict:
    """Mescla ``display transceiver interface <name> verbose`` em ``info``.

    Em switches S-series o ``display interface`` não traz potência ótica;
    os dados de diagnóstico (TX/RX power, corrente, temperatura) só
    aparecem na saída verbose do transceiver. Linhas de threshold/warning
    são ignoradas — só os valores medidos entram.
    """
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"Transceiver Type\s*:\s*(.+)", s)
        if m:
            info["transceiver_type"] = m.group(1).strip()
        m = re.match(r"Wavelength\(nm\)\s*:\s*(\S+)", s)
        if m and m.group(1) != "-":
            info["wavelength"] = m.group(1) + " nm"
        m = re.match(r"Vendor Name\s*:\s*(.+)", s)
        if m and not info["vendor"]:
            info["vendor"] = m.group(1).strip()
        m = re.match(r"Vendor Part Number\s*:\s*(.+)", s)
        if m:
            info["part_number"] = m.group(1).strip()
        # Valores medidos — "(▒▒C)" / "(C)" variam com o charset do VRP.
        m = re.match(r"Temperature\([^)]*\)\s*:\s*(-?[\d.]+)", s)
        if m:
            info["temperature"] = m.group(1) + " °C"
        m = re.match(r"Bias Current\(mA\)\s*:\s*([\d.]+)", s)
        if m:
            info["bias_current"] = m.group(1) + " mA"
        m = re.match(r"RX Power\(dBM\)\s*:\s*(-?[\d.]+)", s, re.I)
        if m:
            info["rx_power"] = m.group(1) + " dBm"
        m = re.match(r"TX Power\(dBM\)\s*:\s*(-?[\d.]+)", s, re.I)
        if m:
            info["tx_power"] = m.group(1) + " dBm"
    # Só anexa ao raw se a saída contiver dados reais (não erros do VRP).
    if text.strip() and "Error" not in text and "Wrong" not in text:
        info["raw"] = (info.get("raw", "") +
                       "\n\n--- transceiver ---\n" + text)
    return info


def parse_ip_interface_brief(text: str) -> list[dict]:
    """``display ip interface brief`` -> [{name, ip, physical, protocol}]"""
    rows = []
    pattern = re.compile(
        rf"^({_IFACE_NAME})\s+(\S+)\s+(\S+)\s+(\S+)"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "name": expand_iface_name(m.group(1)),
            "ip": m.group(2),
            "physical": m.group(3),
            "protocol": m.group(4),
        })
    return rows


def parse_vlans(text: str) -> list[dict]:
    """``display vlan`` -> [{id, type, description}].

    O VRP exibe duas tabelas: a primeira com ID/Tipo/Portas e a segunda
    (opcional) com ID/Status/.../Descrição. Mescla as duas.
    """
    # 1ª tabela: VID  Type  Ports
    first: dict[str, str] = {}
    in_first = False
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^VID\s+Type\s+Ports", s, re.I):
            in_first = True
            continue
        if not in_first:
            continue
        # A segunda tabela começa com "VID  Status  Property"
        if re.match(r"^VID\s+Status\s+Property", s, re.I):
            break
        m = re.match(r"^(\d{1,4})\s+(\S+)", s)
        if m and 1 <= int(m.group(1)) <= 4094:
            first[m.group(1)] = m.group(2)

    # 2ª tabela: VID  Status  Property  MAC-LRN  Statistics  Description
    desc: dict[str, str] = {}
    in_second = False
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^VID\s+Status\s+Property", s, re.I):
            in_second = True
            continue
        if not in_second:
            continue
        # 6 campos: id status property mac-lrn stats description(pode ser vazio)
        m = re.match(
            r"^(\d{1,4})\s+\S+\s+\S+\s+\S+\s+\S+\s*(.*)?$", s)
        if m and 1 <= int(m.group(1)) <= 4094:
            raw = (m.group(2) or "").strip()
            desc[m.group(1)] = "" if raw == "-" else raw

    if not first:
        # Fallback: formato simples "VID  Type"
        for line in text.splitlines():
            m = re.match(r"^(\d{1,4})\s+(\S+)\s*(.*)$", line.strip())
            if m and 1 <= int(m.group(1)) <= 4094:
                first[m.group(1)] = m.group(2)
                if m.group(3).strip() and m.group(3).strip() != "-":
                    desc[m.group(1)] = m.group(3).strip()

    rows = []
    for vid, vtype in first.items():
        rows.append({"id": vid, "type": vtype,
                     "description": desc.get(vid, "")})
    return rows


def parse_routing_table(text: str) -> list[dict]:
    """``display ip routing-table`` ->
    [{destination, proto, pre, cost, nexthop, interface}]"""
    rows = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s+(\S+)\s+(\d+)\s+(\d+)\s+"
        r"\S*\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "destination": m.group(1),
            "proto": m.group(2),
            "pre": m.group(3),
            "cost": m.group(4),
            "nexthop": m.group(5),
            "interface": m.group(6),
        })
    return rows


def parse_bgp_peers(text: str) -> list[dict]:
    """``display bgp peer`` / ``display bgp ipv6 peer`` ->
    [{peer, version, as, msgrcvd, msgsent, up_down, state, prefrcv, family}]

    Aceita vizinhos IPv4 e IPv6. Endereços IPv6 longos fazem o VRP quebrar
    a linha (endereço sozinho, demais colunas na linha seguinte) — essas
    linhas são reunidas antes do parse. A família é deduzida do próprio
    endereço (':' = IPv6), e não do comando que gerou a saída.
    """
    rows = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+)\s+"
        r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(\d+)?"
    )
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        line = f"{pending} {stripped}" if pending else stripped
        pending = ""
        m = pattern.match(line)
        if not m:
            # Endereço IPv6 sozinho na linha => colunas vêm na próxima.
            if ":" in stripped and re.fullmatch(r"[0-9A-Fa-f:.]+", stripped):
                pending = stripped
            continue
        peer = m.group(1)
        rows.append({
            "peer": peer,
            "version": m.group(2),
            "as": m.group(3),
            "msgrcvd": m.group(4),
            "msgsent": m.group(5),
            "up_down": m.group(7),
            "state": m.group(8),
            "prefrcv": m.group(9) or "",
            "family": "IPv6" if ":" in peer else "IPv4",
        })
    return rows


def parse_bgp_local(text: str) -> dict:
    """Extrai AS local e router-id de ``display bgp peer``/``display bgp``."""
    info = {"local_as": "", "router_id": ""}
    m = re.search(r"[Ll]ocal AS number\s*:?\s*(\d+)", text)
    if m:
        info["local_as"] = m.group(1)
    m = re.search(r"BGP local router ID\s*:?\s*(\d{1,3}(?:\.\d{1,3}){3})", text)
    if m:
        info["router_id"] = m.group(1)
    return info


def parse_ospf_peers(text: str) -> list[dict]:
    """``display ospf peer brief`` -> [{area, interface, neighbor, state}]"""
    rows = []
    pattern = re.compile(
        rf"^(\d{{1,3}}(?:\.\d{{1,3}}){{3}}|\d+)\s+({_IFACE_NAME})\s+"
        r"(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "area": m.group(1),
            "interface": m.group(2),
            "neighbor": m.group(3),
            "state": m.group(4),
        })
    return rows


def parse_ldp_sessions(text: str) -> list[dict]:
    """``display mpls ldp session`` -> [{peer, status, role, age}]"""
    rows = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3}:\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "peer": m.group(1),
            "status": m.group(2),
            "role": m.group(3),
            "age": m.group(5),
        })
    return rows


def parse_arp(text: str) -> list[dict]:
    """``display arp`` -> [{ip, mac, expire, type, interface}]"""
    rows = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})\s*(.*)$"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rest = m.group(3).split()
        expire = rest[0] if rest and rest[0].isdigit() else ""
        others = rest[1:] if expire else rest
        rows.append({
            "ip": m.group(1),
            "mac": m.group(2),
            "expire": expire,
            "type": others[0] if others else "",
            "interface": others[1] if len(others) > 1 else "",
        })
    return rows


def parse_ip_pools(text: str) -> list[dict]:
    """``display ip pool`` -> [{name, gateway, network, used, idle}]"""
    rows = []
    current: dict | None = None
    fields = {
        "Pool-name": "name",
        "Gateway-0": "gateway",
        "Network": "network",
        "Used": "used",
        "Idle(Expired)": "idle",
        "Idle": "idle",
    }
    for line in text.splitlines():
        m = re.match(r"^\s*([\w()-]+)\s*:\s*(\S+)", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key == "Pool-name":
            current = {"name": value, "gateway": "", "network": "",
                       "used": "", "idle": ""}
            rows.append(current)
        elif current is not None and key in fields:
            current[fields[key]] = value
    return rows


def parse_nat_outbound(text: str) -> list[dict]:
    """``display nat outbound`` -> [{interface, acl, target, type}]"""
    rows = []
    pattern = re.compile(rf"^({_IFACE_NAME})\s+(\d+)\s+(\S+)\s+(\S+)")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "interface": expand_iface_name(m.group(1)),
            "acl": m.group(2),
            "target": m.group(3),
            "type": m.group(4),
        })
    return rows


def parse_nat_servers(text: str) -> list[dict]:
    """``display nat server`` -> [{interface, protocol, global, inside}]"""
    rows = []
    current: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(rf"^Interface\s*:\s*({_IFACE_NAME})", line)
        if m:
            current = {"interface": expand_iface_name(m.group(1)),
                       "protocol": "", "global": "", "inside": ""}
            rows.append(current)
            continue
        if current is None:
            continue
        m = re.match(r"^Global\s*IP/Port\s*:\s*(\S+)", line)
        if m:
            current["global"] = m.group(1)
            continue
        m = re.match(r"^Inside\s*IP/Port\s*:\s*(\S+)", line)
        if m:
            current["inside"] = m.group(1)
            continue
        m = re.match(r"^Protocol\s*:\s*\d*\(?(\w+)\)?", line)
        if m:
            current["protocol"] = m.group(1)
    return [r for r in rows if r["global"] or r["inside"]]


def parse_acls(text: str) -> list[dict]:
    """``display acl all`` -> [{acl, type, rule, action, detail}]"""
    rows = []
    acl_number = ""
    acl_type = ""
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(Basic|Advanced|Adv|L2)\s+ACL\s+(\d+)", line)
        if m:
            acl_type, acl_number = m.group(1), m.group(2)
            continue
        m = re.match(r"^rule\s+(\d+)\s+(permit|deny)\s*(.*?)(?:\s*\(.*\))?$",
                     line)
        if m and acl_number:
            rows.append({
                "acl": acl_number,
                "type": acl_type,
                "rule": m.group(1),
                "action": m.group(2),
                "detail": m.group(3).strip(),
            })
    return rows


def parse_local_users(text: str) -> list[dict]:
    """``display aaa local-user`` / ``display local-user`` ->
    [{username, state, type}]"""
    rows = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or re.search(r"[Uu]sername|^-+$|^=+$|^\s*$", line):
            continue
        m = re.match(r"^\s*(\S+)\s+([ABab])\s+(\S+)", line)
        if m:
            state = {"a": "Ativo", "b": "Bloqueado"}[m.group(2).lower()]
            rows.append({"username": m.group(1), "state": state,
                         "type": m.group(3)})
    return rows


def parse_lldp_neighbors(text: str) -> list[dict]:
    """``display lldp neighbor brief`` ->
    [{local, neighbor_dev, neighbor_intf, exptime}]"""
    rows = []
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*$")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m or m.group(1).lower().startswith("local"):
            continue
        rows.append({
            "local": m.group(1),
            "neighbor_dev": m.group(2),
            "neighbor_intf": m.group(3),
            "exptime": m.group(4),
        })
    return rows


def parse_lldp_neighbors_detail(text: str) -> list[dict]:
    """``display lldp neighbor`` -> vizinhos com endereço de gerência.

    O ``brief`` dá só nome e porta. O verboso traz também a descrição da
    porta remota, o endereço de gerência e as capacidades do vizinho — o
    suficiente para o diagrama dizer *quem* é cada caixa e não apenas
    *que existe uma*.

    O VRP anuncia o dono do bloco na linha ``<porta local> has N
    neighbor(s)``; os campos vêm como ``Chave : valor`` até o próximo
    bloco. Um ``Neighbor index`` novo dentro da mesma porta começa outro
    vizinho (acontece quando há um hub ou uma ponte no meio).
    """
    rows: list[dict] = []
    local = ""
    current: dict | None = None
    owner_re = re.compile(
        rf"^({_IFACE_NAME})\s+has\s+\d+\s+neighbor", re.IGNORECASE)
    field_re = re.compile(r"^([A-Za-z][A-Za-z /]{2,40}?)\s*:\s*(.*)$")

    def flush() -> None:
        if current and (current["neighbor_dev"] or current["neighbor_intf"]):
            rows.append(current)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.match(r"^[-=]{3,}$", line):
            continue
        match = owner_re.match(line)
        if match:
            flush()
            local = expand_iface_name(match.group(1))
            current = _empty_neighbor(local)
            continue
        if re.match(r"^Neighbor index", line, re.IGNORECASE):
            if current and (current["neighbor_dev"]
                            or current["neighbor_intf"]):
                flush()
                current = _empty_neighbor(local)
            continue
        if current is None:
            continue
        field = field_re.match(line)
        if not field:
            continue
        key = field.group(1).strip().lower()
        value = field.group(2).strip()
        if key == "system name":
            current["neighbor_dev"] = value
        elif key == "port id":
            current["neighbor_intf"] = expand_iface_name(value)
        elif key == "port description":
            current["port_description"] = value
        elif key == "management address":
            # "ipv4" na linha do tipo, o endereço na seguinte — só o que
            # parece endereço entra.
            if re.match(r"^[\d.]+$|^[0-9A-Fa-f:]+:", value):
                current["management_ip"] = value
        elif key == "chassis id":
            current["chassis_id"] = value
        elif key.startswith("system capabilities enabled"):
            current["capabilities"] = value
        elif key == "system description":
            current["system_description"] = value
        elif key == "expired time":
            current["exptime"] = re.sub(r"\D", "", value)
    flush()
    return rows


def _empty_neighbor(local: str) -> dict:
    return {"local": local, "neighbor_dev": "", "neighbor_intf": "",
            "port_description": "", "management_ip": "", "chassis_id": "",
            "capabilities": "", "system_description": "", "exptime": ""}


def parse_ntp_status(text: str) -> str:
    m = re.search(r"clock status\s*:\s*(\S+)", text, re.I)
    return m.group(1) if m else "—"


def parse_ssh_server_status(text: str) -> str:
    m = re.search(r"STELNET server\s*:\s*(\S+)", text, re.I)
    if not m:
        m = re.search(r"Stelnet [Ss]erver\s*:\s*(\S+)", text)
    return m.group(1) if m else "—"


def parse_lldp_status(text: str) -> str:
    m = re.search(r"LLDP [Ss]tatus\s*:\s*(\S+)", text)
    return m.group(1) if m else "—"


def parse_version(text: str) -> dict:
    """``display version`` -> {software, product, uptime}"""
    info = {"software": "", "product": "", "uptime": ""}
    m = re.search(r"VRP.*?[Ss]oftware.*?Version\s+(\S+.*)", text)
    if m:
        info["software"] = m.group(1).strip()
    m = re.search(r"^(?:HUAWEI|Huawei)\s+(\S+.*?)\s+(?:Router|uptime)", text, re.M)
    if m:
        info["product"] = m.group(1).strip()
    m = re.search(r"uptime is\s+(.*)", text)
    if m:
        info["uptime"] = m.group(1).strip()
    return info


def parse_cpu_usage(text: str) -> str:
    m = re.search(r"CPU [Uu]sage\s*:?\s*(\d+)\s*%", text)
    return f"{m.group(1)}%" if m else "—"


def parse_memory_usage(text: str) -> str:
    m = re.search(r"[Mm]emory [Uu]sing [Pp]ercentage.*?(\d+)\s*%", text)
    if not m:
        m = re.search(r"[Mm]emory [Uu]tilization\s*:?\s*(\d+)\s*%", text)
    return f"{m.group(1)}%" if m else "—"


# ------------------------------------------------------------------ SNMP #
def parse_snmp_communities(text: str) -> list[dict]:
    """``display snmp-agent community`` -> [{name, access}]"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"Community name\s*:\s*(\S+)", s, re.I)
        if m:
            current = {"name": m.group(1), "access": "read"}
            rows.append(current)
            continue
        if rows:
            m = re.match(r"Community access\s*:\s*(\S+)", s, re.I)
            if m:
                rows[-1]["access"] = m.group(1).lower()
    # Fallback: formato simples "read-community: name"
    if not rows:
        for line in text.splitlines():
            s = line.strip()
            m = re.match(r"(read|write)\s*[:\-]\s*(\S+)", s, re.I)
            if m:
                rows.append({"name": m.group(2), "access": m.group(1).lower()})
    return rows


def parse_snmp_sys_info(text: str) -> dict:
    """``display snmp-agent sys-info`` -> {version, contact, location}"""
    info = {"version": "—", "contact": "—", "location": "—"}
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"SNMP version\s*:\s*(.+)", s, re.I)
        if m:
            info["version"] = m.group(1).strip()
        m = re.match(r"SysContact\s*:\s*(.+)", s, re.I)
        if m:
            info["contact"] = m.group(1).strip()
        m = re.match(r"SysLocation\s*:\s*(.+)", s, re.I)
        if m:
            info["location"] = m.group(1).strip()
    return info


def parse_snmp_trap_hosts(text: str) -> list[dict]:
    """``display snmp-agent target-host`` -> [{address, community, version}]"""
    rows = []
    current: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"Target address\s*:\s*(\S+)", s, re.I)
        if m:
            current = {"address": m.group(1), "community": "", "version": ""}
            rows.append(current)
            continue
        if current is None:
            continue
        m = re.match(r"Security name\s*:\s*(\S+)", s, re.I)
        if m:
            current["community"] = m.group(1)
        m = re.match(r"Security model\s*:\s*(\S+)", s, re.I)
        if m:
            current["version"] = m.group(1)
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# STP / MSTP
# ──────────────────────────────────────────────────────────────────────────── #
def parse_stp_brief(text: str) -> list[dict]:
    """``display stp brief`` -> [{instance, vlan, priority, role, state, interface}]"""
    rows = []
    # Linha típica: "  0    1-10   CIST   32768  GE0/0/1        DESI  FORW"
    # ou:           "MSTI  Inst VlanMapped  Priority  PortName  Role  STP State"
    header_seen = False
    for line in text.splitlines():
        s = line.strip()
        if re.search(r"\bMSTI\b|\bInst\b|\bVlan\b", s, re.I):
            header_seen = True
            continue
        if not header_seen:
            continue
        parts = s.split()
        if len(parts) >= 5:
            rows.append({
                "instance": parts[0],
                "vlan": parts[1] if len(parts) > 5 else "",
                "priority": parts[-4] if len(parts) > 4 else "",
                "role": parts[-2] if len(parts) > 2 else "",
                "state": parts[-1],
                "interface": parts[-3] if len(parts) > 3 else "",
            })
    # Fallback: formato mais simples "MSTID  Port     Role  STPState"
    if not rows:
        for line in text.splitlines():
            s = line.strip()
            m = re.match(r"(\d+)\s+(\S+)\s+(ROOT|DESI|ALTE|BACK|MAST|DISA)\s+(FORW|DISC|LEAR|BKUP|\S+)", s, re.I)
            if m:
                rows.append({
                    "instance": m.group(1),
                    "vlan": "",
                    "priority": "",
                    "role": m.group(3),
                    "state": m.group(4),
                    "interface": m.group(2),
                })
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# VRRP
# ──────────────────────────────────────────────────────────────────────────── #
def parse_vrrp(text: str) -> list[dict]:
    """``display vrrp brief`` -> [{interface, vrid, state, virtual_ip, priority, master}]"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        # GigabitEthernet0/0/1   1   Master   192.168.1.1   100   192.168.1.2
        m = re.match(
            rf"^({_IFACE_NAME})\s+(\d+)\s+(\w+)\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+(\S+)", s)
        if m:
            rows.append({
                "interface": m.group(1),
                "vrid": m.group(2),
                "state": m.group(3),
                "virtual_ip": m.group(4),
                "priority": m.group(5),
                "master": m.group(6),
            })
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# IPv6
# ──────────────────────────────────────────────────────────────────────────── #
def parse_ipv6_interfaces(text: str) -> list[dict]:
    """``display ipv6 interface brief`` -> [{name, address, prefix, state}]"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(rf"^({_IFACE_NAME})\s+([0-9A-Fa-f:]+(?:/\d+)?)\s+(\S+)", s)
        if m:
            addr = m.group(2)
            parts = addr.split("/")
            rows.append({
                "name": expand_iface_name(m.group(1)),
                "address": parts[0],
                "prefix": parts[1] if len(parts) > 1 else "",
                "state": m.group(3),
            })
    return rows


def parse_ipv6_routing(text: str) -> list[dict]:
    """``display ipv6 routing-table`` -> [{name, address, prefix, state}]"""
    rows = []
    dest_re = re.compile(r"^((?:[0-9A-Fa-f:]+|::)/\d+)")
    nh_re = re.compile(r"NextHop\s*:\s*([0-9A-Fa-f:]+)", re.I)
    proto_re = re.compile(r"Protocol\s*:\s*(\S+)", re.I)
    current: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        m = dest_re.match(s)
        if m:
            dest = m.group(1)
            parts = dest.split("/")
            current = {"name": parts[0], "address": "",
                       "prefix": parts[1] if len(parts) > 1 else "", "state": ""}
            rows.append(current)
            continue
        if current is None:
            continue
        m = nh_re.search(s)
        if m:
            current["address"] = m.group(1)
        m = proto_re.search(s)
        if m:
            current["state"] = m.group(1)
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# MAC Address Table
# ──────────────────────────────────────────────────────────────────────────── #
# Tipos de entrada aceitos por "display mac-address" nas várias versões.
_MAC_ENTRY_TYPES = {
    "dynamic", "static", "blackhole", "security", "sticky", "authen",
    "mux", "snooping", "evn", "learned", "config", "sec-config", "pre-mac",
}


def parse_mac_table(text: str) -> list[dict]:
    """``display mac-address`` -> [{mac, vlan, type, interface}]

    O layout muda bastante entre plataformas — a coluna de VLAN aparece
    como ``100``, ``100/-``, ``100/-/-`` ou ``-/-/vsi1`` conforme a versão,
    e a ordem de "Learned-From" e "Type" também varia::

        MAC Address    VLAN/VSI/SI   Learned-From   Type
        5489-9885-1234 100/-/-       GE0/0/2        dynamic

    Em vez de depender de colunas fixas, cada token é classificado pelo
    que ele é: o tipo vem de uma lista conhecida, a interface casa com o
    padrão de nome de porta e a VLAN é o campo numérico restante.
    """
    rows = []
    mac_re = re.compile(r"^([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})\b")
    vlan_re = re.compile(r"^(\d{1,4}|-)(?:[/-]\S*)?$")
    iface_re = re.compile(rf"^{_IFACE_NAME}$")
    for line in text.splitlines():
        stripped = line.strip()
        match = mac_re.match(stripped)
        if not match:
            continue
        tokens = stripped.split()
        mac, vlan, interface, entry_type = tokens[0], "", "", ""
        for token in tokens[1:]:
            lowered = token.lower()
            if not entry_type and lowered in _MAC_ENTRY_TYPES:
                entry_type = lowered
                continue
            if not interface and iface_re.match(token):
                interface = expand_iface_name(token)
                continue
            if not vlan:
                vlan_match = vlan_re.match(token)
                if vlan_match and vlan_match.group(1) != "-":
                    vlan = vlan_match.group(1)
        rows.append({
            "mac": mac,
            "vlan": vlan,
            "type": entry_type,
            "interface": interface,
        })
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# Diagnóstico óptico (DOM) dos transceptores
# ──────────────────────────────────────────────────────────────────────────── #
_DOM_FIELDS = {
    "temperature": r"Temperature",
    "voltage": r"Voltage",
    "bias": r"(?:Bias|Laser Bias)\s*Current",
    "rx_power": r"(?:Current\s+)?RX\s*Power",
    "tx_power": r"(?:Current\s+)?TX\s*Power",
}
_DOM_THRESHOLDS = {
    "rx_high": r"RX\s*Power\s*High",
    "rx_low": r"RX\s*Power\s*Low",
    "tx_high": r"TX\s*Power\s*High",
    "tx_low": r"TX\s*Power\s*Low",
    "bias_high": r"Bias\s*(?:Current\s*)?High",
    "bias_low": r"Bias\s*(?:Current\s*)?Low",
    "temp_high": r"Temp(?:erature)?\s*High",
    "temp_low": r"Temp(?:erature)?\s*Low",
}
_NUMBER = r"(-?\d+(?:\.\d+)?)"


def _dom_value(line: str, label: str) -> float | None:
    """Lê ``<label> ... : <número>`` tolerando unidade entre parênteses."""
    match = re.search(rf"{label}[^:]*:\s*{_NUMBER}", line, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def parse_transceiver_diagnosis(text: str,
                                default_name: str | None = None) -> dict[str, dict]:
    """Saída de ``display transceiver ...`` -> {interface: medições}.

    ``default_name`` é o nome da porta a quem atribuir os valores que
    aparecem **antes** de qualquer cabeçalho. Consultando uma porta
    específica (``display transceiver interface X verbose``), boa parte
    das versões não repete o nome dela na saída — quem perguntou já sabe
    qual é, e sem isso o resultado seria descartado por não ter dono.

    Cobre as duas formas em que o VRP entrega o diagnóstico óptico:

    * **tabela** (``display transceiver diagnosis interface``), uma linha
      por porta com temperatura, tensão, corrente e potências;
    * **blocos verbosos** (``display transceiver all`` /
      ``... interface X verbose``), que além das medições trazem os
      **limiares** de alarme do módulo.

    Os limiares são o que transforma um número solto em diagnóstico: −24
    dBm é ruim num módulo cujo piso é −14 e perfeitamente normal num de
    −28. Quando eles vêm, ``margin_db`` diz quantos dB ainda faltam para o
    piso — é o número que antecipa um enlace prestes a cair.
    """
    ports: dict[str, dict] = {}
    current = ""
    if default_name:
        current = expand_iface_name(default_name)
        ports[current] = _empty_dom(current)
    # Linha de tabela: nome da porta seguido de 3 a 5 números.
    table_re = re.compile(
        rf"^({_IFACE_NAME})\s+{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}"
        rf"(?:\s+{_NUMBER})?(?:\s+{_NUMBER})?\s*$")
    header_re = re.compile(rf"^({_IFACE_NAME})\b.*(?:transceiver|:)?\s*$",
                           re.IGNORECASE)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.match(r"^[-=]{3,}$", line):
            continue

        match = table_re.match(line)
        if match:
            name = expand_iface_name(match.group(1))
            values = [float(v) if v is not None else None
                      for v in match.groups()[1:]]
            entry = ports.setdefault(name, _empty_dom(name))
            # Ordem usual: temperatura, tensão, corrente, RX, TX.
            for key, value in zip(
                    ("temperature", "voltage", "bias", "rx_power", "tx_power"),
                    values):
                if value is not None:
                    entry[key] = value
            current = name
            continue

        # Cabeçalho de bloco: "GigabitEthernet0/0/1 transceiver information:"
        if header_re.match(line) and not re.search(r":\s*-?\d", line):
            name = expand_iface_name(header_re.match(line).group(1))
            current = name
            ports.setdefault(name, _empty_dom(name))
            continue

        if not current:
            continue
        entry = ports[current]
        if re.search(r"Transceiver Type\s*:", line, re.IGNORECASE):
            entry["type"] = line.split(":", 1)[1].strip()
            continue
        if re.search(r"Vendor (?:Name|PN)\s*:", line, re.IGNORECASE):
            entry.setdefault("vendor", line.split(":", 1)[1].strip())
            continue
        if re.search(r"Wavelength", line, re.IGNORECASE):
            value = _dom_value(line, "Wavelength")
            if value:
                entry["wavelength"] = value
            continue
        # Limiares antes das medições: "RX Power High Threshold" contém
        # "RX Power" e casaria com a medição se testado depois.
        matched = False
        for key, label in _DOM_THRESHOLDS.items():
            if re.search(rf"{label}[^:]*Threshold", line, re.IGNORECASE):
                value = _dom_value(line, label)
                if value is not None:
                    entry[key] = value
                matched = True
                break
        if matched:
            continue
        for key, label in _DOM_FIELDS.items():
            if re.search(rf"{label}\s*(?:\([^)]*\))?\s*:", line, re.IGNORECASE):
                value = _dom_value(line, label)
                if value is not None:
                    entry[key] = value
                break

    for entry in ports.values():
        _evaluate_dom(entry)
    return {name: entry for name, entry in ports.items()
            if entry["rx_power"] is not None or entry["tx_power"] is not None
            or entry["type"] or entry["rx_low"] is not None}


def _empty_dom(name: str) -> dict:
    return {
        "name": name, "type": "", "vendor": "", "wavelength": None,
        "temperature": None, "voltage": None, "bias": None,
        "rx_power": None, "tx_power": None,
        "rx_high": None, "rx_low": None, "tx_high": None, "tx_low": None,
        "bias_high": None, "bias_low": None,
        "temp_high": None, "temp_low": None,
        "status": "", "margin_db": None,
    }


# Folga (dB) abaixo da qual a porta é considerada em risco, mesmo sem ter
# cruzado o limiar. Um enlace que trabalha a menos de 3 dB do piso costuma
# cair na primeira chuva.
DOM_WARN_MARGIN_DB = 3.0


DOM_THRESHOLD_KEYS = ("rx_high", "rx_low", "tx_high", "tx_low",
                      "bias_high", "bias_low", "temp_high", "temp_low")


def evaluate_dom(entry: dict) -> None:
    """Reavalia uma porta após seus limiares terem sido descobertos."""
    _evaluate_dom(entry)


def _evaluate_dom(entry: dict) -> None:
    """Classifica a porta em ok / atenção / crítico e calcula a folga."""
    problems = []
    warnings = []

    rx = entry["rx_power"]
    if rx is not None:
        if entry["rx_low"] is not None:
            entry["margin_db"] = round(rx - entry["rx_low"], 2)
            if rx <= entry["rx_low"]:
                problems.append("RX abaixo do limiar")
            elif entry["margin_db"] < DOM_WARN_MARGIN_DB:
                warnings.append(f"RX a {entry['margin_db']:.1f} dB do piso")
        if entry["rx_high"] is not None and rx >= entry["rx_high"]:
            problems.append("RX acima do limiar")

    tx = entry["tx_power"]
    if tx is not None:
        if entry["tx_low"] is not None and tx <= entry["tx_low"]:
            problems.append("TX abaixo do limiar")
        if entry["tx_high"] is not None and tx >= entry["tx_high"]:
            problems.append("TX acima do limiar")

    for value, low, high, label in (
            (entry["bias"], entry["bias_low"], entry["bias_high"], "corrente"),
            (entry["temperature"], entry["temp_low"], entry["temp_high"],
             "temperatura")):
        if value is None:
            continue
        if low is not None and value <= low:
            problems.append(f"{label} abaixo do limiar")
        if high is not None and value >= high:
            problems.append(f"{label} acima do limiar")

    if problems:
        entry["status"] = "crítico"
        entry["detail"] = "; ".join(problems)
    elif warnings:
        entry["status"] = "atenção"
        entry["detail"] = "; ".join(warnings)
    elif rx is None and tx is None:
        entry["status"] = ""
        entry["detail"] = ""
    elif entry["rx_low"] is None and entry["tx_low"] is None:
        # Sem os limiares do módulo não há como afirmar que está bom: dizer
        # "ok" aqui seria inventar um diagnóstico. Fica explícito que falta
        # informação, e o painel busca os limiares em segundo plano.
        entry["status"] = "sem limiar"
        entry["detail"] = "limiares do módulo ainda não lidos"
    else:
        entry["status"] = "ok"
        entry["detail"] = ""


# ──────────────────────────────────────────────────────────────────────────── #
# Alarmes ativos
# ──────────────────────────────────────────────────────────────────────────── #
# Severidade dos alarmes do VRP, do mais grave para o menos.
ALARM_LEVELS = {
    "critical": 1, "crítico": 1, "major": 2, "minor": 3,
    "warning": 4, "aviso": 4, "indeterminate": 5, "event": 6,
}
ALARM_LABELS = {
    1: "crítico", 2: "maior", 3: "menor", 4: "aviso",
    5: "indeterminado", 6: "evento",
}

_ALARM_LEVEL_RE = re.compile(
    r"\b(Critical|Major|Minor|Warning|Indeterminate|Event)\b", re.IGNORECASE)
_ALARM_DATE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}"
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2})")
_ALARM_ID_RE = re.compile(r"\b(0x[0-9a-fA-F]{4,}|\d{6,})\b")


# Campo "Chave: valor" do formato em bloco. A chave é só letras e espaços,
# o que impede uma linha de tabela ("1  0x0852 Critical 2025-09-12 14:02:11")
# de ser confundida com um campo por causa dos dois-pontos da hora.
_ALARM_KV_RE = re.compile(r"^([A-Za-z][A-Za-z ]{2,30})\s*:\s*(.*)$")
# Número de sequência no início da linha da tabela — é índice de exibição,
# não faz parte da descrição do alarme.
_ALARM_SEQ_RE = re.compile(r"^\d{1,6}\s+")


def _alarm_entry(level_word: str, alarm_id: str, stamp: str,
                 description: str, raw: str) -> dict:
    level = ALARM_LEVELS.get(level_word.lower(), 5)
    return {
        "level": level,
        "severity": ALARM_LABELS.get(level, level_word.lower()),
        "id": alarm_id,
        "stamp": stamp,
        "description": re.sub(r"\s{2,}", " ", description).strip(" \t:;,-"),
        "raw": raw,
    }


def parse_active_alarms(text: str) -> list[dict]:
    """``display alarm active`` -> [{level, severity, id, stamp, description}]

    Este comando aparece em duas formas conforme a plataforma: uma tabela
    (``Sequence AlarmId Level Date Time Description``) e um bloco de
    ``Chave: valor`` por alarme. As duas são tratadas.

    Na tabela, cada linha é lida pelos elementos que contém — severidade,
    data e identificador — e o que sobra vira a descrição; as linhas de
    continuação (descrição longa quebrada em duas) são anexadas à anterior.
    """
    rows: list[dict] = []
    block: dict[str, str] = {}

    def flush_block() -> None:
        if block.get("level"):
            rows.append(_alarm_entry(
                block["level"], block.get("id", ""), block.get("stamp", ""),
                block.get("description") or block.get("name", ""),
                block.get("raw", "")))
        block.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or re.match(r"^[-=+]{3,}$", stripped):
            continue
        if re.match(r"^Total\b", stripped, re.IGNORECASE):
            continue

        field = _ALARM_KV_RE.match(stripped)
        if field:
            key = field.group(1).strip().lower()
            value = field.group(2).strip()
            if "name" in key:
                flush_block()
                block["name"] = value
            elif "level" in key or "severity" in key:
                block["level"] = value
            elif "time" in key or "date" in key:
                block["stamp"] = value
            elif "description" in key or "reason" in key or "cause" in key:
                block["description"] = value
            elif key.endswith("id"):
                block["id"] = value
            else:
                continue
            block["raw"] = f"{block.get('raw', '')}\n{stripped}".strip()
            continue

        level_match = _ALARM_LEVEL_RE.search(stripped)
        if not level_match:
            if rows and line.startswith((" ", "\t")):
                # Descrição longa quebrada pelo VRP na linha seguinte.
                rows[-1]["description"] = (
                    f"{rows[-1]['description']} {stripped}").strip()
                rows[-1]["raw"] += " " + stripped
            continue
        if re.match(r"^(Sequence|AlarmId|Level|Date|Description)\b",
                    stripped, re.IGNORECASE):
            continue                      # cabeçalho da tabela

        date_match = _ALARM_DATE_RE.search(stripped)
        id_match = _ALARM_ID_RE.search(stripped)
        description = _ALARM_SEQ_RE.sub("", stripped)
        for match in (level_match, date_match, id_match):
            if match:
                description = description.replace(match.group(0), " ", 1)
        rows.append(_alarm_entry(
            level_match.group(1), id_match.group(1) if id_match else "",
            date_match.group(1) if date_match else "", description, stripped))
    flush_block()
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# Ping / traceroute
# ──────────────────────────────────────────────────────────────────────────── #
_PING_REPLY_RE = re.compile(
    r"(?:Reply from|bytes from)\s+(?P<peer>[0-9a-fA-F.:]+)"
    r".*?time\s*[=<]\s*(?P<rtt>[\d.]+)\s*ms", re.IGNORECASE)
_PING_TIMEOUT_RE = re.compile(
    r"Request\s+time\s*out|Destination\s+(?:host\s+)?unreachable"
    r"|no\s+answer", re.IGNORECASE)
_PING_STATS_RE = re.compile(
    r"round-trip\s+min/avg/max\s*=\s*([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)",
    re.IGNORECASE)


def parse_ping(text: str) -> dict:
    """Saída do ``ping`` do VRP -> estatísticas e tempos de cada resposta.

    Retorna ``{sent, received, lost, loss_pct, rtts, rtt_min, rtt_avg,
    rtt_max, peer}``. Os tempos individuais (``rtts``) são o que alimenta o
    gráfico de latência do ping contínuo; quando o equipamento só devolve o
    resumo, os valores min/méd/máx ainda são aproveitados.
    """
    rtts: list[float] = []
    timeouts = 0
    peer = ""
    for line in text.splitlines():
        match = _PING_REPLY_RE.search(line)
        if match:
            # "Reply from 8.8.8.8:" — o dois-pontos separa o endereço do
            # resto da linha e não faz parte dele.
            peer = peer or match.group("peer").rstrip(":")
            try:
                rtts.append(float(match.group("rtt")))
            except ValueError:
                pass
            continue
        if _PING_TIMEOUT_RE.search(line):
            timeouts += 1

    sent = received = None
    match = re.search(r"(\d+)\s+packet\(?s?\)?\s+transmitted", text, re.I)
    if match:
        sent = int(match.group(1))
    match = re.search(r"(\d+)\s+packet\(?s?\)?\s+received", text, re.I)
    if match:
        received = int(match.group(1))
    # Formato compacto: "5 packet(s) transmitted, 5 received, 0.00% packet loss"
    if sent is None or received is None:
        match = re.search(r"(\d+)\s+\S*\s*transmitted,\s*(\d+)\s+received",
                          text, re.I)
        if match:
            sent = int(match.group(1))
            received = int(match.group(2))
    if sent is None:
        sent = len(rtts) + timeouts
    if received is None:
        received = len(rtts)

    loss_pct = None
    match = re.search(r"([\d.]+)\s*%\s*packet\s*loss", text, re.I)
    if match:
        loss_pct = float(match.group(1))
    elif sent:
        loss_pct = (sent - received) / sent * 100.0

    rtt_min = rtt_avg = rtt_max = None
    match = _PING_STATS_RE.search(text)
    if match:
        rtt_min, rtt_avg, rtt_max = (float(match.group(i)) for i in (1, 2, 3))
    elif rtts:
        rtt_min, rtt_max = min(rtts), max(rtts)
        rtt_avg = sum(rtts) / len(rtts)

    return {
        "peer": peer,
        "sent": sent,
        "received": received,
        "lost": max(0, (sent or 0) - (received or 0)),
        "loss_pct": loss_pct,
        "rtts": rtts,
        "rtt_min": rtt_min,
        "rtt_avg": rtt_avg,
        "rtt_max": rtt_max,
    }


def parse_traceroute(text: str) -> list[dict]:
    """``tracert`` -> [{hop, host, times}] — um registro por salto."""
    rows = []
    pattern = re.compile(r"^\s*(\d{1,2})\s+(.*)$")
    for line in text.splitlines():
        match = pattern.match(line.rstrip())
        if not match:
            continue
        hop, rest = match.group(1), match.group(2).strip()
        if not rest:
            continue
        host_match = re.search(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}|[0-9a-fA-F:]{4,}",
                               rest)
        times = re.findall(r"([\d.]+)\s*ms", rest)
        rows.append({
            "hop": hop,
            "host": host_match.group(0) if host_match else
                    ("*" if "*" in rest else ""),
            "times": [float(t) for t in times],
        })
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# Logbuffer
# ──────────────────────────────────────────────────────────────────────────── #
# Severidade do VRP (mesma escala do syslog): 0 é o mais grave.
LOG_SEVERITY = {
    0: "emergência", 1: "alerta", 2: "crítico", 3: "erro",
    4: "aviso", 5: "notificação", 6: "informação", 7: "depuração",
}

# "Sep 12 2025 14:02:11 SW01 %%01IFNET/4/LINK_STATE(l):mensagem"
# Variações cobertas: fração de segundo e fuso ("14:02:11.123.1+03:00"),
# índice entre colchetes ("(l)[42]:") e o número de versão após o "%%".
_LOG_LINE_RE = re.compile(
    r"^(?P<stamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+"
    r"\d{2}:\d{2}:\d{2}(?:[.\d]*)?(?:[+-]\d{2}:\d{2})?)\s+"
    r"(?P<host>\S+)\s+"
    r"%%\d*(?P<module>[A-Za-z0-9_-]+)/(?P<severity>\d)/"
    r"(?P<event>[A-Za-z0-9_-]+)"
    r"\s*(?:\([a-z]\))?(?:\[\d+\])?\s*:\s*(?P<message>.*)$")


def parse_logbuffer(text: str) -> list[dict]:
    """``display logbuffer`` -> [{stamp, severity, level, module, event, message}]

    Extrai a severidade do próprio código do VRP (``%%01BGP/3/STATE_CHG``:
    o ``3`` é o nível), que é o que permite separar um aviso de rotina de
    um vizinho BGP caindo. Linhas que não casam com o padrão viram entradas
    de texto puro em vez de serem descartadas — algumas versões quebram a
    mensagem em várias linhas, e perder a continuação esconderia justamente
    o detalhe do evento.
    """
    rows: list[dict] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        match = _LOG_LINE_RE.match(stripped.strip())
        if match:
            level = int(match.group("severity"))
            rows.append({
                "stamp": match.group("stamp"),
                "level": level,
                "severity": LOG_SEVERITY.get(level, str(level)),
                "module": match.group("module"),
                "event": match.group("event"),
                "message": match.group("message").strip(),
                "raw": stripped,
            })
            continue
        if _looks_like_header(stripped):
            # Cabeçalho do próprio comando ("Allowed max buffer size : ...",
            # linhas de traços): não é evento, não entra na lista.
            continue
        if rows:
            # Continuação da mensagem anterior (o VRP quebra as longas).
            rows[-1]["message"] = f"{rows[-1]['message']} {stripped.strip()}"
            rows[-1]["raw"] += "\n" + stripped
            continue
        rows.append({
            "stamp": "", "level": 6, "severity": LOG_SEVERITY[6],
            "module": "", "event": "", "message": stripped.strip(),
            "raw": stripped,
        })
    return rows


def _looks_like_header(line: str) -> bool:
    """Linhas decorativas ou de cabeçalho do próprio comando."""
    return bool(re.match(r"^[-=]{3,}$|^Logging buffer|^Allowed max buffer|"
                         r"^Actual buffer|^Channel number|^Dropped messages|"
                         r"^Overwritten messages|^Current messages",
                         line.strip(), re.IGNORECASE))


# ──────────────────────────────────────────────────────────────────────────── #
# Port Security
# ──────────────────────────────────────────────────────────────────────────── #
def parse_port_security(text: str) -> list[dict]:
    """``display port-security`` -> [{interface, enabled, max_mac, action, dhcp_snooping}]"""
    rows = []
    current: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(rf"^({_IFACE_NAME})", s)
        if m and re.search(r"[Pp]ort|[Ee]nable|[Dd]isable", s):
            current = {
                "interface": expand_iface_name(m.group(1)),
                "enabled": "—", "max_mac": "—",
                "action": "—", "dhcp_snooping": "—",
            }
            rows.append(current)
            continue
        if current is None:
            continue
        if re.search(r"port.security\s*:\s*enable", s, re.I):
            current["enabled"] = "sim"
        elif re.search(r"port.security\s*:\s*disable", s, re.I):
            current["enabled"] = "não"
        m2 = re.search(r"max.mac.num\s*:\s*(\d+)", s, re.I)
        if m2:
            current["max_mac"] = m2.group(1)
        m2 = re.search(r"action\s*:\s*(\w+)", s, re.I)
        if m2:
            current["action"] = m2.group(1)
        if re.search(r"dhcp.snoop.*enable", s, re.I):
            current["dhcp_snooping"] = "sim"
    # Fallback: saída simples quando não tem port-security configurado
    if not rows:
        return [{"interface": "—", "enabled": "não configurado",
                 "max_mac": "—", "action": "—", "dhcp_snooping": "—"}]
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# QoS applied policies
# ──────────────────────────────────────────────────────────────────────────── #
def parse_qos_policies(text: str) -> list[dict]:
    """``display traffic policy applied-record`` -> [{type, name, detail, applied}]"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        # Policy application: "GigabitEthernet0/0/1  inbound  MyPolicy"
        m = re.match(rf"^({_IFACE_NAME})\s+(inbound|outbound)\s+(\S+)", s, re.I)
        if m:
            rows.append({
                "type": "Aplicada",
                "name": m.group(3),
                "detail": f"{m.group(2)}",
                "applied": m.group(1),
            })
            continue
        # Traffic policy definition: "Policy: MyPolicy"
        m = re.match(r"^Policy\s*:\s*(\S+)", s, re.I)
        if m:
            rows.append({"type": "Política", "name": m.group(1),
                         "detail": "", "applied": ""})
            continue
        # Classifier entry: "  Classifier: MyCls  Behavior: MyBeh"
        m = re.match(r"Classifier\s*:\s*(\S+)\s+Behavior\s*:\s*(\S+)", s, re.I)
        if m:
            rows.append({"type": "Classificador→Comportamento",
                         "name": m.group(1),
                         "detail": f"→ {m.group(2)}", "applied": ""})
    # If empty, fetch all defined classifiers/behaviors
    if not rows:
        rows.append({"type": "—", "name": "Nenhuma política aplicada",
                     "detail": "", "applied": ""})
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# BRAS / PPPoE (NE8000) — assinantes, domínios
# ──────────────────────────────────────────────────────────────────────────── #
_MAC_RE = re.compile(r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def parse_access_users(text: str) -> list[dict]:
    """``display access-user`` -> [{userid, username, ip, mac, interface, domain}]

    Os assinantes de banda larga (PPPoE/IPoE) aparecem em formatos que
    variam bastante entre versões do VRP. Em vez de depender de colunas
    fixas, ancora cada linha na presença simultânea de um MAC
    (xxxx-xxxx-xxxx) e de um IPv4 — assim funciona na maioria dos layouts.
    Nomes longos fazem o VRP quebrar a linha (UserID/Username numa linha,
    IP/MAC/interface na seguinte); essas linhas são reunidas.
    """
    rows = []
    pending_user = ""
    pending_id = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or re.match(r"^[-=]+$", s):
            continue
        if re.search(r"UserID|User[\s-]?name|Total|Domain|Access interface",
                     s, re.I):
            continue
        toks = s.split()
        mac = _MAC_RE.search(s)
        ip = _IPV4_RE.search(s)
        if not (mac and ip):
            # Linha "enrolada": guarda userid e/ou username para a próxima.
            if toks and toks[0].isdigit():
                pending_id = toks[0]
            cand = next((t for t in toks if "@" in t), "")
            if not cand:
                cand = next((t for t in toks
                            if not t.isdigit() and re.match(r"^[\w@.\-]+$", t)),
                            "")
            if cand:
                pending_user = cand
            continue
        ip_val = ip.group(0)
        userid = toks[0] if toks and toks[0].isdigit() else pending_id
        try:
            ip_idx = toks.index(ip_val)
        except ValueError:
            ip_idx = -1
        username = toks[ip_idx - 1] if ip_idx >= 1 else ""
        if (not username or _MAC_RE.fullmatch(username)
                or username in (ip_val, userid)):
            username = pending_user or username
        interface = toks[-1] if toks else ""
        domain = username.split("@", 1)[1] if "@" in username else ""
        rows.append({
            "userid": userid,
            "username": username,
            "ip": ip_val,
            "mac": mac.group(0),
            "interface": expand_iface_name(interface),
            "domain": domain,
        })
        pending_user = ""
        pending_id = ""
    return rows


def parse_domains(text: str) -> list[dict]:
    """``display domain`` -> [{name, state}] (domínios AAA / perfis BRAS)."""
    rows = []
    state_re = re.compile(r"^(\S+)\s+(Active|Block(?:ed)?|Inactive)\b", re.I)
    for line in text.splitlines():
        s = line.strip()
        if not s or re.match(r"^[-=]+$", s):
            continue
        if re.search(r"Domain[\s-]?name|Total|^\s*State\b", s, re.I):
            continue
        m = state_re.match(s)
        if m:
            rows.append({"name": m.group(1), "state": m.group(2)})
    # Fallback: versões que listam só o nome do domínio, sem coluna de estado.
    if not rows:
        for line in text.splitlines():
            s = line.strip()
            m = re.match(r"^([A-Za-z][\w.\-]{1,63})$", s)
            if m and not re.search(r"total|domain|state", s, re.I):
                rows.append({"name": m.group(1), "state": ""})
    return rows


# ──────────────────────────────────────────────────────────────────────────── #
# MQC — traffic classifier / behavior / policy (firewall avançado e CGNAT)
# ──────────────────────────────────────────────────────────────────────────── #
def parse_mqc_blocks(text: str, head: str) -> list[dict]:
    """Extrai blocos de ``display traffic classifier|behavior|policy``.

    ``head`` é ``Classifier``, ``Behavior`` ou ``Policy``. Cada bloco começa
    em ``<head>: <nome>`` e as linhas indentadas seguintes viram o detalhe
    (if-match, redirect, precedence...). Tolerante a variações de layout.
    """
    rows = []
    cur = None
    start = re.compile(rf"^\s*{head}\s*:\s*(\S+)", re.I)
    for line in text.splitlines():
        m = start.match(line)
        if m:
            cur = {"name": m.group(1), "_d": []}
            rows.append(cur)
            continue
        if cur is not None:
            s = line.strip()
            if (s and not re.match(r"^[-=]+$", s)
                    and not re.match(r"^(Total|User|Traffic)", s, re.I)):
                cur["_d"].append(s)
    for r in rows:
        r["detail"] = " · ".join(r.pop("_d")[:5])
    return rows
