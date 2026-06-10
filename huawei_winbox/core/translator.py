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


# ------------------------------ ARP ----------------------------------- #
def arp_static(ip: str, mac: str, remove: bool = False) -> list[str]:
    prefix = "undo " if remove else ""
    return [f"{prefix}arp static {ip} {mac}"]


# ------------------------------ DHCP ---------------------------------- #
def dhcp_enable() -> list[str]:
    return ["dhcp enable"]


def dhcp_pool(name: str, network: str, mask: str, gateway: str = "",
              dns: str = "", lease_days: int = 1) -> list[str]:
    cmds = ["dhcp enable", f"ip pool {name}",
            f"network {network} mask {mask}"]
    if gateway.strip():
        cmds.append(f"gateway-list {gateway.strip()}")
    if dns.strip():
        cmds.append(f"dns-list {dns.strip()}")
    cmds.append(f"lease day {lease_days} hour 0 minute 0")
    cmds.append("quit")
    return cmds


def dhcp_pool_delete(name: str) -> list[str]:
    return [f"undo ip pool {name}"]


def dhcp_interface(interface: str, remove: bool = False) -> list[str]:
    verb = "undo dhcp select global" if remove else "dhcp select global"
    return [f"interface {interface}", verb, "quit"]


# ------------------------------- NAT ----------------------------------- #
def nat_outbound(interface: str, acl: int, remove: bool = False) -> list[str]:
    verb = "undo nat outbound" if remove else "nat outbound"
    return [f"interface {interface}", f"{verb} {acl}", "quit"]


def nat_server(interface: str, protocol: str, global_ip: str,
               global_port: int, inside_ip: str, inside_port: int,
               remove: bool = False) -> list[str]:
    # global_ip vazio = usa o IP da própria interface (current-interface).
    target = global_ip.strip() or "current-interface"
    verb = "undo nat server" if remove else "nat server"
    return [
        f"interface {interface}",
        f"{verb} protocol {protocol} global {target} {global_port} "
        f"inside {inside_ip} {inside_port}",
        "quit",
    ]


# --------------------------- ACL / Firewall ---------------------------- #
def acl_rule(acl_number: int, action: str, source: str = "",
             wildcard: str = "", rule_id: int = 0,
             remove: bool = False) -> list[str]:
    cmds = [f"acl {acl_number}"]
    rule = "rule "
    if rule_id:
        rule += f"{rule_id} "
    if remove:
        cmds.append(f"undo rule {rule_id}")
    else:
        rule += action
        if source.strip():
            if source.strip().lower() == "any":
                rule += " source any"
            else:
                rule += f" source {source.strip()} {wildcard.strip()}"
        cmds.append(rule)
    cmds.append("quit")
    return cmds


def acl_delete(acl_number: int) -> list[str]:
    return [f"undo acl {acl_number}"]


def traffic_filter(interface: str, direction: str, acl_number: int,
                   remove: bool = False) -> list[str]:
    verb = "undo traffic-filter" if remove else "traffic-filter"
    suffix = "" if remove else f" acl {acl_number}"
    return [f"interface {interface}", f"{verb} {direction}{suffix}", "quit"]


# ----------------------------- Usuários -------------------------------- #
def local_user(username: str, password: str, privilege: int = 15,
               services: str = "ssh") -> list[str]:
    return [
        "aaa",
        f"local-user {username} password irreversible-cipher {password}",
        f"local-user {username} privilege level {privilege}",
        f"local-user {username} service-type {services}",
        "quit",
    ]


def local_user_delete(username: str) -> list[str]:
    return ["aaa", f"undo local-user {username}", "quit"]


# ----------------------------- Serviços -------------------------------- #
def ntp_server(ip: str, remove: bool = False) -> list[str]:
    prefix = "undo " if remove else ""
    return [f"{prefix}ntp-service unicast-server {ip}"]


def snmp_community(community: str, write: bool = False,
                   remove: bool = False) -> list[str]:
    access = "write" if write else "read"
    if remove:
        return [f"undo snmp-agent community {access} {community}"]
    return ["snmp-agent sys-info version v2c",
            f"snmp-agent community {access} {community}"]


def lldp_enable(enable: bool = True) -> list[str]:
    return ["lldp enable" if enable else "undo lldp enable"]


def ssh_server_enable() -> list[str]:
    return ["stelnet server enable"]


# ---------------------------- Sistema --------------------------------- #
def set_sysname(name: str) -> list[str]:
    return [f"sysname {name.strip()}"]


def save_config() -> list[str]:
    # Fora da visão de sistema; o "y" confirma a gravação.
    return ["save", "y"]
