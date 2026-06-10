"""Tradutor: converte ações da interface gráfica em comandos CLI do VRP.

Cada função recebe os parâmetros preenchidos na tela e devolve a lista
de comandos exatamente como será enviada ao equipamento — essa lista é
exibida ao usuário na pré-visualização antes do envio via SSH.
"""

from __future__ import annotations


# --------------------------- Interfaces ------------------------------- #
def interface_description(interface: str, description: str) -> list[str]:
    cmds = [f"interface {interface}"]
    if description.strip():
        cmds.append(f"description {description.strip()}")
    else:
        cmds.append("undo description")
    cmds.append("quit")
    return cmds


def interface_shutdown(interface: str, enable: bool) -> list[str]:
    return [
        f"interface {interface}",
        "undo shutdown" if enable else "shutdown",
        "quit",
    ]


def interface_ip(interface: str, address: str, mask: str,
                 remove: bool = False) -> list[str]:
    verb = "undo ip address" if remove else "ip address"
    return [f"interface {interface}", f"{verb} {address} {mask}", "quit"]


def interface_mtu(interface: str, mtu: int) -> list[str]:
    return [f"interface {interface}", f"mtu {mtu}", "quit"]


# ------------------------------ VLAN ---------------------------------- #
def vlan_create(vlan_id: int, name: str = "") -> list[str]:
    cmds = [f"vlan {vlan_id}"]
    if name.strip():
        cmds.append(f"name {name.strip()}")
    cmds.append("quit")
    return cmds


def vlan_delete(vlan_id: int) -> list[str]:
    return [f"undo vlan {vlan_id}"]


def port_access(interface: str, vlan_id: int) -> list[str]:
    return [
        f"interface {interface}",
        "port link-type access",
        f"port default vlan {vlan_id}",
        "quit",
    ]


def port_trunk(interface: str, vlans: str) -> list[str]:
    return [
        f"interface {interface}",
        "port link-type trunk",
        f"port trunk allow-pass vlan {vlans}",
        "quit",
    ]


# ---------------------------- Rotas ----------------------------------- #
def static_route(destination: str, mask: str, next_hop: str,
                 preference: int | None = None,
                 description: str = "",
                 remove: bool = False) -> list[str]:
    base = f"ip route-static {destination} {mask} {next_hop}"
    if remove:
        return [f"undo {base}"]
    if preference:
        base += f" preference {preference}"
    if description.strip():
        base += f" description {description.strip()}"
    return [base]


# ------------------------------ BGP ----------------------------------- #
def bgp_enable(local_as: int, router_id: str = "") -> list[str]:
    cmds = [f"bgp {local_as}"]
    if router_id.strip():
        cmds.append(f"router-id {router_id.strip()}")
    cmds.append("quit")
    return cmds


def bgp_peer(local_as: int, peer_ip: str, remote_as: int,
             description: str = "", remove: bool = False) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if remove:
        cmds.append(f"undo peer {peer_ip}")
    else:
        cmds.append(f"peer {peer_ip} as-number {remote_as}")
        if description.strip():
            cmds.append(f"peer {peer_ip} description {description.strip()}")
    cmds.append("quit")
    return cmds


def bgp_network(local_as: int, network: str, mask: str,
                remove: bool = False) -> list[str]:
    verb = "undo network" if remove else "network"
    return [f"bgp {local_as}", "ipv4-family unicast",
            f"{verb} {network} {mask}", "quit", "quit"]


# ------------------------------ OSPF ---------------------------------- #
def ospf_enable(process_id: int, router_id: str = "") -> list[str]:
    cmd = f"ospf {process_id}"
    if router_id.strip():
        cmd += f" router-id {router_id.strip()}"
    return [cmd, "quit"]


def ospf_network(process_id: int, area: str, network: str,
                 wildcard: str, remove: bool = False) -> list[str]:
    verb = "undo network" if remove else "network"
    return [
        f"ospf {process_id}",
        f"area {area}",
        f"{verb} {network} {wildcard}",
        "quit",
        "quit",
    ]


# ---------------------------- MPLS / LDP ------------------------------ #
def mpls_enable(lsr_id: str) -> list[str]:
    return [f"mpls lsr-id {lsr_id}", "mpls", "quit", "mpls ldp", "quit"]


def mpls_interface(interface: str, ldp: bool = True,
                   remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        if ldp:
            cmds.append("undo mpls ldp")
        cmds.append("undo mpls")
    else:
        cmds.append("mpls")
        if ldp:
            cmds.append("mpls ldp")
    cmds.append("quit")
    return cmds


# ---------------------------- Sistema --------------------------------- #
def set_sysname(name: str) -> list[str]:
    return [f"sysname {name.strip()}"]


def save_config() -> list[str]:
    # Fora da visão de sistema; o "y" confirma a gravação.
    return ["save", "y"]
