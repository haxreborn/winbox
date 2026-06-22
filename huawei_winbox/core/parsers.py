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


def parse_interface_brief(text: str) -> list[dict]:
    """``display interface brief`` ->
    [{name, phy, protocol, in_util, out_util}]"""
    rows = []
    pattern = re.compile(
        rf"^({_IFACE_NAME})\s+(\*?[Uu]p|[Dd]own|\*down|\^down)\s+(\S+)"
        r"(?:\s+([\d.]+%|-+)\s+([\d.]+%|-+))?"
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
        m = re.match(r"Speed\s*:\s*(\d+)", s)
        if m:
            info["speed"] = m.group(1)
        m = re.match(r"Duplex\s*:\s*(\S+)", s)
        if m:
            info["duplex"] = m.group(1)
        m = re.match(r"Negotiation\s*:\s*(\S+)", s)
        if m:
            info["negotiation"] = m.group(1)
        m = re.match(r"MTU\s+(\d+)", s)
        if m:
            info["mtu"] = m.group(1)
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
def parse_mac_table(text: str) -> list[dict]:
    """``display mac-address-table`` -> [{mac, vlan, type, interface}]"""
    rows = []
    pattern = re.compile(
        r"^([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})\s+"
        r"(\d+)\s+(\w+)\s+\S+\s+(\S+)"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "mac": m.group(1),
            "vlan": m.group(2),
            "type": m.group(3),
            "interface": expand_iface_name(m.group(4)),
        })
    return rows


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
