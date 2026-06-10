"""Parsers da saída dos comandos ``display`` do VRP.

Convertem o texto retornado pelo equipamento em listas de dicionários
para preencher as tabelas da interface gráfica. São tolerantes a
variações de formato entre versões do VRP — linhas que não casam com o
padrão esperado são simplesmente ignoradas.
"""

from __future__ import annotations

import re

_IFACE_NAME = (
    r"(?:GigabitEthernet|XGigabitEthernet|40GE|100GE|25GE|10GE|GE|Eth-Trunk|"
    r"Ethernet|MEth|LoopBack|Loopback|Vlanif|Tunnel|NULL|Null|MultiGE|Pos|"
    r"Virtual-Template|Vbdif|Sip)[\d/\.:]*"
)


def parse_interface_brief(text: str) -> list[dict]:
    """``display interface brief`` ->
    [{name, phy, protocol, in_util, out_util, description}]"""
    rows = []
    pattern = re.compile(
        rf"^({_IFACE_NAME})\s+(\S+)\s+(\S+)(?:\s+(\S+)\s+(\S+))?(?:\s+(.*))?$"
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
            "description": (m.group(6) or "").strip(),
        })
    return rows


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
    """``display vlan`` -> [{id, type, description}] (formato resumido)."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\d{1,4})\s+(\S+)\s*(.*)$", line.strip())
        if m and 1 <= int(m.group(1)) <= 4094:
            rows.append({
                "id": m.group(1),
                "type": m.group(2),
                "description": m.group(3).strip(),
            })
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
