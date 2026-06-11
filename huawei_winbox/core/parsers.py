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
            "name": m.group(1),
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
    pattern = re.compile(
        rf"^({_IFACE_NAME})\s+(\S+)\s+(\S+)\s*(.*)?$"
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
            "name": m.group(1),
            "phy": m.group(2),
            "protocol": m.group(3),
            "description": (m.group(4) or "").strip(),
        })
    return rows


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
    if text.strip():
        info["raw"] = (info.get("raw", "") +
                       "\n\n===== display transceiver verbose =====\n" + text)
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
            "name": m.group(1),
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
    """``display bgp peer`` -> [{peer, version, as, msgrcvd, msgsent,
    up_down, state, prefrcv}]"""
    rows = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
        r"(\S+)\s+(\S+)\s+(\S+)\s*(\d+)?"
    )
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        rows.append({
            "peer": m.group(1),
            "version": m.group(2),
            "as": m.group(3),
            "msgrcvd": m.group(4),
            "msgsent": m.group(5),
            "up_down": m.group(7),
            "state": m.group(8),
            "prefrcv": m.group(9) or "",
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
            "interface": m.group(1),
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
            current = {"interface": m.group(1), "protocol": "",
                       "global": "", "inside": ""}
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
