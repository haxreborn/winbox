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


def bgp_peer_ignore(local_as: int, peer_ip: str,
                    ignore: bool = True) -> list[str]:
    """Habilita ou desabilita (ignore) um peer BGP sem removê-lo."""
    verb = "" if ignore else "undo "
    return [f"bgp {local_as}", f"{verb}peer {peer_ip} ignore", "quit"]


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


# ------------------------------ SNMP ---------------------------------- #
def snmp_enable(version: str = "v2c") -> list[str]:
    """Habilita o agente SNMP com a versão especificada."""
    return [f"snmp-agent sys-info version {version}"]


def snmp_set_contact(contact: str) -> list[str]:
    return [f'snmp-agent sys-info contact "{contact.strip()}"']


def snmp_set_location(location: str) -> list[str]:
    return [f'snmp-agent sys-info location "{location.strip()}"']


def snmp_add_community(community: str, write: bool = False,
                       remove: bool = False) -> list[str]:
    access = "write" if write else "read"
    verb = "undo snmp-agent community" if remove else "snmp-agent community"
    if remove:
        return [f"{verb} {access} {community}"]
    return [f"snmp-agent sys-info version v2c",
            f"snmp-agent community {access} {community}"]


def snmp_add_trap_host(ip: str, community: str, udp_port: int = 162,
                       version: str = "v2c", remove: bool = False) -> list[str]:
    if remove:
        return [f"undo snmp-agent target-host trap address "
                f"udp-domain {ip} udp-port {udp_port}"]
    return [
        "snmp-agent trap enable",
        f"snmp-agent target-host trap address udp-domain {ip} "
        f"udp-port {udp_port} params securityname {community} {version}",
    ]


def snmp_disable() -> list[str]:
    return ["undo snmp-agent"]


# ---------------------------- Sistema --------------------------------- #
def set_sysname(name: str) -> list[str]:
    return [f"sysname {name.strip()}"]


def save_config() -> list[str]:
    # A confirmação "[Y/N]" da gravação é respondida automaticamente
    # pela sessão (_CONFIRM_RE em session.py).
    return ["save"]


# ─────────────────────────────────────────────────────────────────────────────
# Eth-Trunk (Link Aggregation)
# ─────────────────────────────────────────────────────────────────────────────
def eth_trunk_create(trunk_id: int, mode: str = "lacp-static") -> list[str]:
    # mode: "lacp-static" | "lacp-dynamic" | "manual"
    return [f"interface Eth-Trunk{trunk_id}",
            f"mode {mode}", "quit"]

def eth_trunk_delete(trunk_id: int) -> list[str]:
    return [f"undo interface Eth-Trunk{trunk_id}"]

def eth_trunk_add_member(interface: str, trunk_id: int,
                         remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append("undo eth-trunk")
    else:
        cmds.append(f"eth-trunk {trunk_id}")
    cmds.append("quit")
    return cmds

def eth_trunk_min_links(trunk_id: int, min_links: int) -> list[str]:
    return [f"interface Eth-Trunk{trunk_id}",
            f"least active-linknumber {min_links}", "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# Sub-interfaces / Dot1Q encapsulation
# ─────────────────────────────────────────────────────────────────────────────
def subinterface_dot1q(interface: str, vlan_id: int,
                       remove: bool = False) -> list[str]:
    if remove:
        return [f"undo interface {interface}"]
    return [f"interface {interface}",
            f"dot1q termination vid {vlan_id}",
            "ip binding vpn-instance",       # opcional — não causa erro se não tiver VPN
            "arp broadcast enable",
            "quit"]

def subinterface_dot1q_simple(interface: str, vlan_id: int,
                               remove: bool = False) -> list[str]:
    """Versão simples: só o encapsulamento (sem VPN binding)."""
    if remove:
        return [f"undo interface {interface}"]
    return [f"interface {interface}",
            f"dot1q termination vid {vlan_id}",
            "arp broadcast enable",
            "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# STP / RSTP / MSTP
# ─────────────────────────────────────────────────────────────────────────────
def stp_mode(mode: str) -> list[str]:
    # mode: "stp" | "rstp" | "mstp"
    return [f"stp mode {mode}"]

def stp_enable(enable: bool = True) -> list[str]:
    return ["stp enable" if enable else "undo stp enable"]

def stp_priority(priority: int, instance: int = 0) -> list[str]:
    if instance:
        return [f"stp instance {instance} priority {priority}"]
    return [f"stp priority {priority}"]

def stp_interface(interface: str, edge: bool | None = None,
                  bpdu_filter: bool | None = None,
                  bpdu_guard: bool | None = None,
                  root_guard: bool | None = None,
                  cost: int | None = None,
                  priority: int | None = None) -> list[str]:
    cmds = [f"interface {interface}"]
    if edge is not None:
        cmds.append("stp edged-port enable" if edge
                    else "undo stp edged-port enable")
    if bpdu_filter is not None:
        cmds.append("stp bpdu-filter enable" if bpdu_filter
                    else "undo stp bpdu-filter enable")
    if bpdu_guard is not None:
        cmds.append("stp bpdu-protection" if bpdu_guard
                    else "undo stp bpdu-protection")
    if root_guard is not None:
        cmds.append("stp root-protection" if root_guard
                    else "undo stp root-protection")
    if cost is not None:
        cmds.append(f"stp cost {cost}")
    if priority is not None:
        cmds.append(f"stp port priority {priority}")
    cmds.append("quit")
    return cmds

def stp_tc_protection(enable: bool = True) -> list[str]:
    return ["stp tc-protection" if enable else "undo stp tc-protection"]

def mstp_region(region_name: str, revision: int = 0) -> list[str]:
    return ["stp region-configuration",
            f"region-name {region_name}",
            f"revision-level {revision}",
            "active region-configuration",
            "quit"]

def mstp_instance_vlan(instance: int, vlan_range: str,
                        remove: bool = False) -> list[str]:
    verb = "undo " if remove else ""
    return ["stp region-configuration",
            f"{verb}instance {instance} vlan {vlan_range}",
            "active region-configuration",
            "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# VRRP
# ─────────────────────────────────────────────────────────────────────────────
def vrrp_create(interface: str, group_id: int, virtual_ip: str) -> list[str]:
    return [f"interface {interface}",
            f"vrrp vrid {group_id} virtual-ip {virtual_ip}",
            "quit"]

def vrrp_delete(interface: str, group_id: int) -> list[str]:
    return [f"interface {interface}",
            f"undo vrrp vrid {group_id}",
            "quit"]

def vrrp_priority(interface: str, group_id: int,
                  priority: int) -> list[str]:
    return [f"interface {interface}",
            f"vrrp vrid {group_id} priority {priority}",
            "quit"]

def vrrp_preempt(interface: str, group_id: int,
                 enable: bool = True, delay: int = 0) -> list[str]:
    cmds = [f"interface {interface}"]
    if enable:
        cmd = f"vrrp vrid {group_id} preempt-mode timer delay {delay}"
    else:
        cmd = f"undo vrrp vrid {group_id} preempt-mode"
    cmds.extend([cmd, "quit"])
    return cmds

def vrrp_auth(interface: str, group_id: int,
              key: str, remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo vrrp vrid {group_id} authentication-mode")
    else:
        cmds.append(f"vrrp vrid {group_id} authentication-mode simple {key}")
    cmds.append("quit")
    return cmds

def vrrp_track_interface(interface: str, group_id: int,
                          track_iface: str, reduced: int = 10,
                          remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo vrrp vrid {group_id} track interface "
                    f"{track_iface}")
    else:
        cmds.append(f"vrrp vrid {group_id} track interface {track_iface} "
                    f"reduced {reduced}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# IPv6 — interfaces e rotas
# ─────────────────────────────────────────────────────────────────────────────
def ipv6_interface_enable(interface: str, enable: bool = True) -> list[str]:
    verb = "" if enable else "undo "
    return [f"interface {interface}", f"{verb}ipv6 enable", "quit"]

def ipv6_address(interface: str, address: str, prefix_len: int,
                 link_local: bool = False,
                 remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        if link_local:
            cmds.append(f"undo ipv6 address {address} link-local")
        else:
            cmds.append(f"undo ipv6 address {address}/{prefix_len}")
    else:
        cmds.append("ipv6 enable")
        if link_local:
            cmds.append(f"ipv6 address {address} link-local")
        else:
            cmds.append(f"ipv6 address {address}/{prefix_len}")
    cmds.append("quit")
    return cmds

def ipv6_static_route(destination: str, prefix_len: int,
                       next_hop: str, preference: int | None = None,
                       remove: bool = False) -> list[str]:
    base = f"ipv6 route-static {destination} {prefix_len} {next_hop}"
    if remove:
        return [f"undo {base}"]
    if preference:
        base += f" preference {preference}"
    return [base]

def ipv6_nd_ra_suppress(interface: str, suppress: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("undo ipv6 nd ra halt" if not suppress else "ipv6 nd ra halt")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# OSPFv3 (IPv6)
# ─────────────────────────────────────────────────────────────────────────────
def ospfv3_enable(process_id: int, router_id: str = "") -> list[str]:
    cmd = f"ospfv3 {process_id}"
    if router_id.strip():
        cmd += f" router-id {router_id.strip()}"
    return [cmd, "quit"]

def ospfv3_interface(interface: str, process_id: int,
                      area: str, remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo ospfv3 {process_id} area {area}")
    else:
        cmds.append(f"ospfv3 {process_id} area {area}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# OSPF expansions
# ─────────────────────────────────────────────────────────────────────────────
def ospf_area_type(process_id: int, area: str,
                   area_type: str) -> list[str]:
    # area_type: "stub" | "nssa" | "normal" (undo)
    cmds = [f"ospf {process_id}", f"area {area}"]
    if area_type == "normal":
        cmds.append("undo stub")
    else:
        cmds.append(area_type)
    cmds.extend(["quit", "quit"])
    return cmds

def ospf_redistribute(process_id: int, protocol: str,
                       route_policy: str = "",
                       remove: bool = False) -> list[str]:
    # protocol: "bgp" | "static" | "direct" | "isis" | "rip"
    cmds = [f"ospf {process_id}"]
    if remove:
        cmds.append(f"undo import-route {protocol}")
    else:
        cmd = f"import-route {protocol}"
        if route_policy.strip():
            cmd += f" route-policy {route_policy.strip()}"
        cmds.append(cmd)
    cmds.append("quit")
    return cmds

def ospf_interface_cost(interface: str, process_id: int,
                         cost: int) -> list[str]:
    return [f"interface {interface}",
            f"ospf cost {cost}",
            "quit"]

def ospf_authentication(process_id: int, area: str,
                         auth_type: str, key: str = "",
                         remove: bool = False) -> list[str]:
    # auth_type: "simple" | "md5"
    cmds = [f"ospf {process_id}", f"area {area}"]
    if remove:
        cmds.append("undo authentication-mode")
    else:
        if auth_type == "md5":
            cmds.append(f"authentication-mode md5 1 cipher {key}")
        else:
            cmds.append(f"authentication-mode simple cipher {key}")
    cmds.extend(["quit", "quit"])
    return cmds

def ospf_default_route(process_id: int,
                        always: bool = False,
                        remove: bool = False) -> list[str]:
    cmds = [f"ospf {process_id}"]
    if remove:
        cmds.append("undo default-route-advertise")
    elif always:
        cmds.append("default-route-advertise always")
    else:
        cmds.append("default-route-advertise")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# BGP expansions
# ─────────────────────────────────────────────────────────────────────────────
def bgp_peer_group(local_as: int, group_name: str,
                   remote_as: int | None = None,
                   remove: bool = False) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if remove:
        cmds.append(f"undo group {group_name}")
    else:
        cmds.append(f"group {group_name} external")
        if remote_as:
            cmds.append(f"peer {group_name} as-number {remote_as}")
    cmds.append("quit")
    return cmds

def bgp_peer_to_group(local_as: int, peer_ip: str,
                       group_name: str,
                       remove: bool = False) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if remove:
        cmds.append(f"undo peer {peer_ip} group {group_name}")
    else:
        cmds.append(f"peer {peer_ip} group {group_name}")
    cmds.append("quit")
    return cmds

def bgp_route_policy(local_as: int, peer_ip: str,
                      direction: str, policy_name: str,
                      remove: bool = False) -> list[str]:
    # direction: "import" | "export"
    cmds = [f"bgp {local_as}"]
    if remove:
        cmds.append(f"undo peer {peer_ip} route-policy {policy_name} "
                    f"{direction}")
    else:
        cmds.append(f"peer {peer_ip} route-policy {policy_name} {direction}")
    cmds.append("quit")
    return cmds

def bgp_soft_reset(local_as: int, peer_ip: str = "",
                   direction: str = "both") -> list[str]:
    # Roda fora da visão de sistema; por isso o caller usará
    # system_view=False em apply_commands.
    target = peer_ip if peer_ip else "all"
    return [f"reset bgp {local_as} {target} {direction}"]

def bgp_bfd(local_as: int, peer_ip: str,
             enable: bool = True) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if enable:
        cmds.append(f"peer {peer_ip} bfd enable")
    else:
        cmds.append(f"undo peer {peer_ip} bfd enable")
    cmds.append("quit")
    return cmds

def bgp_keepalive(local_as: int, keepalive: int = 60,
                   hold_time: int = 180) -> list[str]:
    return [f"bgp {local_as}",
            f"timer keepalive {keepalive} hold {hold_time}",
            "quit"]

def bgp_ipv6_peer(local_as: int, peer_ip: str,
                   remote_as: int, remove: bool = False) -> list[str]:
    cmds = [f"bgp {local_as}",
            "ipv6-family unicast"]
    if remove:
        cmds.append(f"undo peer {peer_ip} enable")
    else:
        cmds.extend([f"peer {peer_ip} as-number {remote_as}",
                     f"peer {peer_ip} enable"])
    cmds.extend(["quit", "quit"])
    return cmds

def bgp_community(local_as: int, peer_ip: str,
                   send_community: bool = True) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if send_community:
        cmds.append(f"peer {peer_ip} advertise-community")
    else:
        cmds.append(f"undo peer {peer_ip} advertise-community")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# Route-Policy and IP prefix-list
# ─────────────────────────────────────────────────────────────────────────────
def route_policy_node(policy_name: str, node: int,
                       action: str = "permit",
                       remove: bool = False) -> list[str]:
    if remove:
        return [f"undo route-policy {policy_name} {action} node {node}"]
    return [f"route-policy {policy_name} {action} node {node}", "quit"]

def route_policy_match_prefix(policy_name: str, node: int,
                               action: str, prefix_list: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"if-match ip-prefix {prefix_list}",
            "quit"]

def route_policy_set_community(policy_name: str, node: int,
                                action: str,
                                community: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"apply community {community}",
            "quit"]

def ip_prefix_list(name: str, index: int, action: str,
                   prefix: str, ge: int = 0, le: int = 0,
                   remove: bool = False) -> list[str]:
    if remove:
        return [f"undo ip ip-prefix {name} index {index}"]
    cmd = f"ip ip-prefix {name} index {index} {action} {prefix}"
    if ge:
        cmd += f" greater-equal {ge}"
    if le:
        cmd += f" less-equal {le}"
    return [cmd]


# ─────────────────────────────────────────────────────────────────────────────
# QoS
# ─────────────────────────────────────────────────────────────────────────────
def qos_traffic_classifier(name: str,
                             dscp: str = "",
                             acl: int = 0,
                             remove: bool = False) -> list[str]:
    if remove:
        return [f"undo traffic classifier {name}"]
    cmds = [f"traffic classifier {name} operator or"]
    if dscp.strip():
        cmds.append(f"if-match dscp {dscp.strip()}")
    if acl:
        cmds.append(f"if-match acl {acl}")
    cmds.append("quit")
    return cmds

def qos_traffic_behavior(name: str, action: str = "permit",
                          queue: str = "", dscp_mark: str = "",
                          rate_kbps: int = 0,
                          remove: bool = False) -> list[str]:
    # action: "permit" | "deny" | "remark"
    if remove:
        return [f"undo traffic behavior {name}"]
    cmds = [f"traffic behavior {name}"]
    cmds.append(action)
    if queue.strip():
        cmds.append(f"queue {queue.strip()}")
    if dscp_mark.strip():
        cmds.append(f"remark dscp {dscp_mark.strip()}")
    if rate_kbps:
        cmds.append(f"car cir {rate_kbps} kbps")
    cmds.append("quit")
    return cmds

def qos_traffic_policy(policy_name: str,
                        classifier: str, behavior: str,
                        remove_policy: bool = False,
                        remove_class: bool = False) -> list[str]:
    if remove_policy:
        return [f"undo traffic policy {policy_name}"]
    cmds = [f"traffic policy {policy_name}"]
    if remove_class:
        cmds.append(f"undo classifier {classifier}")
    else:
        cmds.append(f"classifier {classifier} behavior {behavior}")
    cmds.append("quit")
    return cmds

def qos_policy_apply(interface: str, policy_name: str,
                      direction: str, remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo traffic-policy {policy_name} {direction}")
    else:
        cmds.append(f"traffic-policy {policy_name} {direction}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# Port Security
# ─────────────────────────────────────────────────────────────────────────────
def port_security_enable(interface: str, enable: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("port-security enable" if enable
                else "undo port-security enable")
    cmds.append("quit")
    return cmds

def port_security_max_mac(interface: str, max_mac: int) -> list[str]:
    return [f"interface {interface}",
            f"port-security max-mac-num {max_mac}",
            "quit"]

def port_security_action(interface: str,
                          action: str = "restrict") -> list[str]:
    # action: "restrict" | "protect" | "shutdown"
    return [f"interface {interface}",
            f"port-security protect-action {action}",
            "quit"]

def port_security_static_mac(interface: str, mac: str,
                               vlan: int,
                               remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo port-security mac-address sticky "
                    f"{mac} vlan {vlan}")
    else:
        cmds.append(f"port-security mac-address sticky")
        cmds.append(f"port-security mac-address sticky "
                    f"{mac} vlan {vlan}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# DHCP Snooping
# ─────────────────────────────────────────────────────────────────────────────
def dhcp_snooping_enable(enable: bool = True) -> list[str]:
    return ["dhcp snooping enable" if enable
            else "undo dhcp snooping enable"]

def dhcp_snooping_interface(interface: str,
                             trusted: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    if trusted:
        cmds.append("dhcp snooping trusted")
    else:
        cmds.append("undo dhcp snooping trusted")
    cmds.append("quit")
    return cmds

def dhcp_snooping_vlan(vlan_id: int, enable: bool = True) -> list[str]:
    if enable:
        return [f"vlan {vlan_id}", "dhcp snooping enable", "quit"]
    return [f"vlan {vlan_id}", "undo dhcp snooping enable", "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic ARP Inspection
# ─────────────────────────────────────────────────────────────────────────────
def arp_inspection_vlan(vlan_id: int, enable: bool = True) -> list[str]:
    if enable:
        return [f"arp inspection vlan {vlan_id}"]
    return [f"undo arp inspection vlan {vlan_id}"]

def arp_inspection_interface(interface: str,
                              trusted: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("arp inspection trust" if trusted
                else "undo arp inspection trust")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# MAC Address Table
# ─────────────────────────────────────────────────────────────────────────────
def mac_static(mac: str, vlan: int, interface: str,
               remove: bool = False) -> list[str]:
    if remove:
        return [f"undo mac-address static {mac} vlan {vlan}"]
    return [f"mac-address static {mac} {interface} vlan {vlan}"]

def mac_blackhole(mac: str, vlan: int,
                  remove: bool = False) -> list[str]:
    if remove:
        return [f"undo mac-address blackhole {mac} vlan {vlan}"]
    return [f"mac-address blackhole {mac} vlan {vlan}"]

def mac_aging_time(seconds: int) -> list[str]:
    return [f"mac-address aging-time {seconds}"]

def mac_flush(vlan: int | None = None,
              interface: str | None = None) -> list[str]:
    if vlan:
        return [f"reset mac-address vlan {vlan}"]
    if interface:
        return [f"reset mac-address {interface}"]
    return ["reset mac-address"]


# ─────────────────────────────────────────────────────────────────────────────
# Storm Control
# ─────────────────────────────────────────────────────────────────────────────
def storm_control(interface: str, traffic_type: str,
                  min_rate: int, max_rate: int,
                  remove: bool = False) -> list[str]:
    # traffic_type: "broadcast" | "multicast" | "unknown-unicast"
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo storm-control {traffic_type} min-rate "
                    f"{min_rate} max-rate {max_rate}")
    else:
        cmds.append(f"storm-control {traffic_type} min-rate "
                    f"{min_rate} max-rate {max_rate}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# DHCP Relay
# ─────────────────────────────────────────────────────────────────────────────
def dhcp_relay_server(interface: str, server_ip: str,
                       remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo dhcp relay server-ip {server_ip}")
    else:
        cmds.extend(["dhcp select relay",
                     f"dhcp relay server-ip {server_ip}"])
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# AAA / RADIUS / TACACS+
# ─────────────────────────────────────────────────────────────────────────────
def radius_server(server_ip: str, auth_port: int = 1812,
                   acct_port: int = 1813, key: str = "",
                   remove: bool = False) -> list[str]:
    if remove:
        return ["radius-server template default",
                f"undo radius-server authentication {server_ip} {auth_port}",
                "quit"]
    cmds = ["radius-server template default",
            f"radius-server authentication {server_ip} {auth_port}",
            f"radius-server accounting {server_ip} {acct_port}"]
    if key.strip():
        cmds.append(f"radius-server shared-key cipher {key.strip()}")
    cmds.append("quit")
    return cmds

def tacacs_server(server_ip: str, port: int = 49,
                   key: str = "",
                   remove: bool = False) -> list[str]:
    if remove:
        return ["hwtacacs-server template default",
                f"undo hwtacacs-server authentication {server_ip} {port}",
                "quit"]
    cmds = ["hwtacacs-server template default",
            f"hwtacacs-server authentication {server_ip} {port}",
            f"hwtacacs-server authorization {server_ip} {port}"]
    if key.strip():
        cmds.append(f"hwtacacs-server shared-key cipher {key.strip()}")
    cmds.append("quit")
    return cmds

def aaa_authentication_scheme(scheme: str = "radius") -> list[str]:
    return ["aaa",
            "authentication-scheme default",
            f"authentication-mode {scheme}",
            "quit", "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# DNS
# ─────────────────────────────────────────────────────────────────────────────
def dns_server(ip: str, remove: bool = False) -> list[str]:
    prefix = "undo " if remove else ""
    return [f"{prefix}dns server {ip}"]

def dns_enable(enable: bool = True) -> list[str]:
    return ["dns resolve" if enable else "undo dns resolve"]

def dns_domain(domain: str, remove: bool = False) -> list[str]:
    prefix = "undo " if remove else ""
    return [f"{prefix}dns domain {domain}"]


# ─────────────────────────────────────────────────────────────────────────────
# Syslog
# ─────────────────────────────────────────────────────────────────────────────
def syslog_server(ip: str, port: int = 514,
                   remove: bool = False) -> list[str]:
    if remove:
        return ["info-center loghost source LoopBack0",
                f"undo info-center loghost {ip}"]
    return [f"info-center loghost {ip} port {port}",
            "info-center enable"]

def syslog_level(level: str = "warnings") -> list[str]:
    # level: "emergency"|"alert"|"critical"|"error"|"warnings"|"notification"|"informational"|"debugging"
    return [f"info-center source default channel 2 log level {level}"]

def syslog_timestamp(format: str = "date") -> list[str]:
    # format: "date" | "short-date" | "none" | "boot"
    return [f"info-center timestamp log {format}"]


# ─────────────────────────────────────────────────────────────────────────────
# NTP expansions
# ─────────────────────────────────────────────────────────────────────────────
def ntp_timezone(timezone: str, offset_sign: str,
                  hours: int, minutes: int = 0) -> list[str]:
    # offset_sign: "add" | "minus"
    return [f"clock timezone {timezone} {offset_sign} "
            f"{hours:02d}:{minutes:02d}:00"]

def ntp_sync(server_ip: str) -> list[str]:
    return [f"ntp-service unicast-server {server_ip} source-interface "
            f"LoopBack0"]


# ─────────────────────────────────────────────────────────────────────────────
# SSH / Telnet / Management
# ─────────────────────────────────────────────────────────────────────────────
def ssh_user(username: str, auth_type: str = "password",
             remove: bool = False) -> list[str]:
    # auth_type: "password" | "rsa" | "all"
    if remove:
        return [f"undo ssh user {username}"]
    return [f"ssh user {username}",
            f"ssh user {username} authentication-type {auth_type}",
            f"ssh user {username} service-type stelnet"]

def ssh_version(version: str = "2") -> list[str]:
    return [f"ssh server version {version}"]

def telnet_enable(enable: bool = True) -> list[str]:
    if enable:
        return ["telnet server enable"]
    return ["undo telnet server enable"]

def ftp_enable(enable: bool = True) -> list[str]:
    if enable:
        return ["ftp server enable"]
    return ["undo ftp server enable"]

def vty_acl(acl_number: int, remove: bool = False) -> list[str]:
    cmds = ["user-interface vty 0 4"]
    if remove:
        cmds.append("undo acl inbound")
    else:
        cmds.append(f"acl {acl_number} inbound")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# BFD
# ─────────────────────────────────────────────────────────────────────────────
def bfd_enable() -> list[str]:
    return ["bfd"]

def bfd_session(local_disc: int, peer_ip: str,
                interface: str, source_ip: str,
                min_tx: int = 1000, min_rx: int = 1000,
                detect_mult: int = 3,
                remove: bool = False) -> list[str]:
    if remove:
        return [f"undo bfd {local_disc} bind peer-ip {peer_ip} "
                f"interface {interface} source-ip {source_ip}"]
    return [f"bfd {local_disc} bind peer-ip {peer_ip} "
            f"interface {interface} source-ip {source_ip}",
            f"discriminator local {local_disc}",
            f"discriminator remote {local_disc + 1}",
            f"min-tx-interval {min_tx}",
            f"min-rx-interval {min_rx}",
            f"detect-multiplier {detect_mult}",
            "commit",
            "quit"]


# ─────────────────────────────────────────────────────────────────────────────
# VPN Instance (VRF)
# ─────────────────────────────────────────────────────────────────────────────
def vpn_instance_create(vrf_name: str, rd: str,
                         import_rt: str, export_rt: str,
                         remove: bool = False) -> list[str]:
    if remove:
        return [f"undo ip vpn-instance {vrf_name}"]
    return [f"ip vpn-instance {vrf_name}",
            "ipv4-family",
            f"route-distinguisher {rd}",
            f"vpn-target {import_rt} import-extcommunity",
            f"vpn-target {export_rt} export-extcommunity",
            "quit", "quit"]

def vpn_interface_bind(interface: str, vrf_name: str,
                        remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append("undo ip binding vpn-instance")
    else:
        cmds.append(f"ip binding vpn-instance {vrf_name}")
    cmds.append("quit")
    return cmds

def bgp_vpn_instance(local_as: int, vrf_name: str,
                      rd: str = "",
                      remove: bool = False) -> list[str]:
    cmds = [f"bgp {local_as}"]
    if remove:
        cmds.append(f"undo ipv4-family vpn-instance {vrf_name}")
    else:
        cmds.extend([f"ipv4-family vpn-instance {vrf_name}"])
        if rd:
            cmds.append(f"route-distinguisher {rd}")
    cmds.extend(["quit", "quit"])
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# Interface utility expansions
# ─────────────────────────────────────────────────────────────────────────────
def interface_dampening(interface: str, enable: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("dampening" if enable else "undo dampening")
    cmds.append("quit")
    return cmds

def interface_loopback_create(loopback_id: int,
                               ip: str = "",
                               mask: str = "") -> list[str]:
    cmds = [f"interface LoopBack{loopback_id}"]
    if ip and mask:
        cmds.append(f"ip address {ip} {mask}")
    cmds.append("quit")
    return cmds

def interface_portswitch(interface: str,
                          to_layer2: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("portswitch" if to_layer2 else "undo portswitch")
    cmds.append("quit")
    return cmds

def interface_speed(interface: str, speed: str) -> list[str]:
    # speed: "10" | "100" | "1000" | "auto"
    return [f"interface {interface}", f"speed {speed}", "quit"]

def interface_duplex(interface: str, duplex: str) -> list[str]:
    # duplex: "full" | "half" | "auto"
    return [f"interface {interface}", f"duplex {duplex}", "quit"]

def interface_flow_control(interface: str,
                            enable: bool = True) -> list[str]:
    cmds = [f"interface {interface}"]
    cmds.append("flow-control" if enable else "undo flow-control")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# VLAN expansions
# ─────────────────────────────────────────────────────────────────────────────
def vlan_batch_create(vlan_range: str) -> list[str]:
    return [f"vlan batch {vlan_range}"]

def vlan_batch_delete(vlan_range: str) -> list[str]:
    return [f"undo vlan batch {vlan_range}"]

def port_hybrid(interface: str, tagged_vlans: str,
                untagged_vlans: str) -> list[str]:
    return [f"interface {interface}",
            "port link-type hybrid",
            f"port hybrid tagged vlan {tagged_vlans}",
            f"port hybrid untagged vlan {untagged_vlans}",
            "quit"]

def port_trunk_pvid(interface: str, pvid: int) -> list[str]:
    return [f"interface {interface}",
            f"port trunk pvid vlan {pvid}",
            "quit"]

def voice_vlan(interface: str, vlan_id: int,
               remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append("undo voice-vlan enable")
    else:
        cmds.extend([f"voice-vlan {vlan_id} enable"])
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# ACL expansions (extended / named)
# ─────────────────────────────────────────────────────────────────────────────
def acl_advanced_rule(acl_number: int, action: str,
                       protocol: str = "ip",
                       source: str = "any", src_wildcard: str = "",
                       dest: str = "any", dest_wildcard: str = "",
                       src_port: str = "", dest_port: str = "",
                       rule_id: int = 0,
                       remove: bool = False) -> list[str]:
    cmds = [f"acl {acl_number}"]
    if remove:
        cmds.append(f"undo rule {rule_id}")
    else:
        rule = f"rule "
        if rule_id:
            rule += f"{rule_id} "
        rule += f"{action} {protocol}"
        if source.lower() != "any":
            rule += f" source {source}"
            if src_wildcard:
                rule += f" {src_wildcard}"
        if dest.lower() != "any":
            rule += f" destination {dest}"
            if dest_wildcard:
                rule += f" {dest_wildcard}"
        if src_port:
            rule += f" source-port eq {src_port}"
        if dest_port:
            rule += f" destination-port eq {dest_port}"
        cmds.append(rule)
    cmds.append("quit")
    return cmds

def acl_named(name: str, acl_type: str = "advance",
              remove: bool = False) -> list[str]:
    if remove:
        return [f"undo acl name {name}"]
    return [f"acl name {name} {acl_type}", "quit"]

def traffic_policy_interface(interface: str, direction: str,
                              policy_name: str,
                              remove: bool = False) -> list[str]:
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo traffic-policy inbound")
    else:
        cmds.append(f"traffic-policy {policy_name} {direction}")
    cmds.append("quit")
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# Route-Policy — match / set clauses
# ─────────────────────────────────────────────────────────────────────────────

def route_policy_match_as_path(policy_name: str, node: int,
                                action: str, filter_name: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"if-match as-path-filter {filter_name}",
            "quit"]


def route_policy_match_community(policy_name: str, node: int,
                                  action: str, community_filter: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"if-match community-filter {community_filter}",
            "quit"]


def route_policy_match_interface(policy_name: str, node: int,
                                  action: str, interface: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"if-match interface {interface}",
            "quit"]


def route_policy_match_tag(policy_name: str, node: int,
                            action: str, tag: int) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"if-match tag {tag}",
            "quit"]


def route_policy_set_local_pref(policy_name: str, node: int,
                                 action: str, local_pref: int) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"apply local-preference {local_pref}",
            "quit"]


def route_policy_set_med(policy_name: str, node: int,
                          action: str, med: int) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"apply cost {med}",
            "quit"]


def route_policy_set_next_hop(policy_name: str, node: int,
                               action: str, next_hop: str) -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"apply ip-address nexthop {next_hop}",
            "quit"]


def route_policy_set_as_path_prepend(policy_name: str, node: int,
                                      action: str, as_number: int,
                                      repeat: int = 1) -> list[str]:
    cmds = [f"route-policy {policy_name} {action} node {node}"]
    cmds.append(f"apply as-path {' '.join([str(as_number)] * repeat)} additive")
    cmds.append("quit")
    return cmds


def route_policy_set_origin(policy_name: str, node: int,
                             action: str, origin: str = "igp") -> list[str]:
    return [f"route-policy {policy_name} {action} node {node}",
            f"apply origin {origin}",
            "quit"]


def ip_as_path_filter(filter_name: str, index: int,
                       action: str, regexp: str,
                       remove: bool = False) -> list[str]:
    if remove:
        return [f"undo ip as-path-filter {filter_name} index {index}"]
    return [f"ip as-path-filter {filter_name} index {index} {action} {regexp}"]


def ip_community_filter(filter_name: str, index: int,
                         action: str, community: str,
                         remove: bool = False) -> list[str]:
    if remove:
        return [f"undo ip community-filter {filter_name} index {index}"]
    return [f"ip community-filter {filter_name} index {index} {action} {community}"]


# ─────────────────────────────────────────────────────────────────────────────
# BRAS / PPPoE (NE8000) — domínios, interfaces de acesso, Virtual-Template,
# pools de assinante e desconexão de usuários online.
# ─────────────────────────────────────────────────────────────────────────────
def bras_domain(name: str, auth_scheme: str = "", acct_scheme: str = "",
                radius_group: str = "", ip_pool: str = "",
                ipv6_pool: str = "", remove: bool = False) -> list[str]:
    """Cria/edita um domínio AAA (o "profile" do assinante)."""
    if remove:
        return ["aaa", f"undo domain {name}", "quit"]
    cmds = ["aaa", f"domain {name}"]
    if auth_scheme.strip():
        cmds.append(f"authentication-scheme {auth_scheme.strip()}")
    if acct_scheme.strip():
        cmds.append(f"accounting-scheme {acct_scheme.strip()}")
    if radius_group.strip():
        cmds.append(f"radius-server group {radius_group.strip()}")
    if ip_pool.strip():
        cmds.append(f"ip-pool {ip_pool.strip()}")
    if ipv6_pool.strip():
        cmds.append(f"ipv6-pool {ipv6_pool.strip()}")
    cmds.extend(["quit", "quit"])
    return cmds


def bras_access_interface(interface: str, user_vlan: str, domain: str,
                          remove: bool = False) -> list[str]:
    """Habilita acesso PPPoE/IPoE (BAS layer2-subscriber) numa sub-interface."""
    cmds = [f"interface {interface}"]
    if remove:
        cmds.extend(["undo bas", "quit"])
        return cmds
    if user_vlan.strip():
        cmds.append(f"user-vlan {user_vlan.strip()}")
    cmds.append("bas")
    cmds.append("access-type layer2-subscriber "
                f"default-domain authentication {domain}")
    cmds.extend(["quit", "quit"])
    return cmds


def pppoe_server_template(template_id: int, ac_name: str = "",
                          service_name: str = "", auth_mode: str = "pap",
                          remove: bool = False) -> list[str]:
    """Cria um Virtual-Template servidor PPPoE."""
    if remove:
        return [f"undo interface Virtual-Template{template_id}"]
    cmds = [f"interface Virtual-Template{template_id}",
            f"ppp authentication-mode {auth_mode}"]
    if ac_name.strip():
        cmds.append(f"pppoe-server ac-name {ac_name.strip()}")
    if service_name.strip():
        cmds.append(f"pppoe-server service-name-parameter {service_name.strip()}")
    cmds.append("quit")
    return cmds


def bras_ip_pool(name: str, gateway: str, mask: str, start: str, end: str,
                 dns: str = "", remove: bool = False) -> list[str]:
    """Pool de endereços de assinante (``ip pool <name> bas local``)."""
    if remove:
        return [f"undo ip pool {name}"]
    cmds = [f"ip pool {name} bas local",
            f"gateway {gateway} {mask}",
            f"section 0 {start} {end}"]
    if dns.strip():
        cmds.append(f"dns-server {dns.strip()}")
    cmds.append("quit")
    return cmds


def cut_access_user(by: str, value: str) -> list[str]:
    """Desconecta assinante(s) online. by: 'username'|'domain'|'interface'."""
    return [f"cut access-user {by} {value}"]


def local_ppp_user(username: str, password: str,
                   remove: bool = False) -> list[str]:
    """Conta PPPoE local (equivalente ao 'Secrets' do WinBox)."""
    if remove:
        return ["aaa", f"undo local-user {username}", "quit"]
    return [
        "aaa",
        f"local-user {username} password irreversible-cipher {password}",
        f"local-user {username} service-type ppp",
        "quit",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Firewall avançado / MQC (traffic classifier-behavior-policy) e CGNAT
# ─────────────────────────────────────────────────────────────────────────────
def fw_classifier(name: str, operator: str = "or", acl: int = 0,
                  dscp: str = "", remove: bool = False) -> list[str]:
    """Classificador MQC: casa um ACL e/ou DSCP."""
    if remove:
        return [f"undo traffic classifier {name}"]
    cmds = [f"traffic classifier {name} operator {operator}"]
    if acl:
        cmds.append(f"if-match acl {acl}")
    if dscp.strip():
        cmds.append(f"if-match dscp {dscp.strip()}")
    cmds.append("quit")
    return cmds


def fw_behavior(name: str, kind: str = "permit", nexthop: str = "",
                interface: str = "", car_kbps: int = 0, dscp: str = "",
                remove: bool = False) -> list[str]:
    """Comportamento MQC.

    kind: ``permit`` | ``deny`` | ``redirect-nexthop`` (CGNAT) |
    ``redirect-interface`` | ``remark`` | ``car``.
    """
    if remove:
        return [f"undo traffic behavior {name}"]
    cmds = [f"traffic behavior {name}"]
    if kind == "permit":
        cmds.append("permit")
    elif kind == "deny":
        cmds.append("deny")
    elif kind == "redirect-nexthop" and nexthop.strip():
        cmds.append(f"redirect ip-nexthop {nexthop.strip()}")
    elif kind == "redirect-interface" and interface.strip():
        cmds.append(f"redirect interface {interface.strip()}")
    elif kind == "remark" and dscp.strip():
        cmds.append(f"remark dscp {dscp.strip()}")
    if car_kbps:
        cmds.append(f"car cir {car_kbps}")
    cmds.append("quit")
    return cmds


def fw_policy_bind(policy: str, classifier: str, behavior: str,
                   precedence: int = 0, share_mode: bool = False,
                   remove: bool = False) -> list[str]:
    """Associa classificador→comportamento dentro de uma traffic policy."""
    cmds = [f"traffic policy {policy}"]
    if share_mode:
        cmds.append("share-mode")
    if remove:
        cmds.append(f"undo classifier {classifier}")
    else:
        c = f"classifier {classifier} behavior {behavior}"
        if precedence:
            c += f" precedence {precedence}"
        cmds.append(c)
    cmds.append("quit")
    return cmds


def fw_policy_delete(policy: str) -> list[str]:
    return [f"undo traffic policy {policy}"]


def fw_policy_apply(interface: str, policy: str, direction: str,
                    remove: bool = False) -> list[str]:
    """Aplica/retira a traffic policy numa interface."""
    cmds = [f"interface {interface}"]
    if remove:
        cmds.append(f"undo traffic-policy {direction}")
    else:
        cmds.append(f"traffic-policy {policy} {direction}")
    cmds.append("quit")
    return cmds


def fw_policy_apply_global(policy: str, direction: str = "inbound",
                           remove: bool = False) -> list[str]:
    """Aplica/retira a traffic policy globalmente (CGNAT: global-acl)."""
    if remove:
        return [f"undo traffic-policy {policy} {direction} global-acl"]
    return [f"traffic-policy {policy} {direction} global-acl"]


def fw_cgnat_chain(acl_number: int, source: str, wildcard: str,
                   classifier: str, behavior: str, policy: str,
                   nexthop: str, precedence: int = 15,
                   apply_global: bool = True,
                   direction: str = "inbound") -> list[str]:
    """Monta toda a cadeia de CGNAT por redirecionamento (estilo A10):

    ACL com a origem CGNAT → classificador → comportamento ``redirect
    ip-nexthop`` → política → (opcional) aplicação global.
    """
    cmds = [
        f"acl number {acl_number}",
        f"description CGNAT-{classifier}",
        f"rule 5 permit ip source {source} {wildcard}",
        "quit",
        f"traffic classifier {classifier} operator or",
        f"if-match acl {acl_number}",
        "quit",
        f"traffic behavior {behavior}",
        f"redirect ip-nexthop {nexthop}",
        "quit",
        f"traffic policy {policy}",
        f"classifier {classifier} behavior {behavior} precedence {precedence}",
        "quit",
    ]
    if apply_global:
        cmds.append(f"traffic-policy {policy} {direction} global-acl")
    return cmds
