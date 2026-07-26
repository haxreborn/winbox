"""Localizar um cliente pelo IP, MAC ou nome — em que porta ele está.

É a pergunta mais frequente de um suporte: *"o cliente 100.64.0.10
reclamou; onde ele está ligado?"*. Responder à mão exige encadear três ou
quatro comandos e casar os resultados na cabeça: o ARP dá o MAC, a tabela
MAC dá a porta, a descrição da porta diz de quem ela é, e no BRAS ainda há
a sessão PPPoE.

Este módulo faz esse encadeamento. Ele é **só leitura** e não depende de
Qt: recebe uma função que executa comandos e devolve os achados, o que
permite testá-lo inteiro contra saídas gravadas de equipamento.

A busca é **encadeada**, não paralela: descobrir o MAC pelo IP abre a
consulta da tabela MAC, que revela a porta, que por sua vez traz a
descrição. Cada etapa aproveita o que a anterior encontrou.
"""

from __future__ import annotations

import re

from . import parsers

KIND_IPV4 = "ipv4"
KIND_IPV6 = "ipv6"
KIND_MAC = "mac"
KIND_TEXT = "texto"

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IPV6_RE = re.compile(r"^[0-9A-Fa-f:]{2,}:[0-9A-Fa-f:]*$")
# MAC em qualquer notação usual: Huawei (aabb-ccdd-eeff), Cisco/Linux com
# dois-pontos ou hífens, e a forma sem separador.
_MAC_CHARS_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def normalize_mac(text: str) -> str | None:
    """Converte um MAC de qualquer notação para a forma do VRP.

    ``00:e0:fc:12:34:56``, ``00-e0-fc-12-34-56`` e ``00e0fc123456`` viram
    ``00e0-fc12-3456``. Sem isso, colar um MAC copiado de um sistema
    Linux ou de um relatório em outro formato simplesmente não encontrava
    nada — e o operador não tem por que saber o formato de cada fabricante.
    """
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", text or "")
    if not _MAC_CHARS_RE.match(cleaned):
        return None
    lower = cleaned.lower()
    return f"{lower[0:4]}-{lower[4:8]}-{lower[8:12]}"


def classify(query: str) -> tuple[str, str]:
    """Descobre o que foi digitado -> (tipo, valor normalizado)."""
    text = (query or "").strip()
    if not text:
        return KIND_TEXT, ""
    mac = normalize_mac(text)
    if mac:
        return KIND_MAC, mac
    if _IPV4_RE.match(text):
        return KIND_IPV4, text
    if ":" in text and _IPV6_RE.match(text):
        return KIND_IPV6, text
    return KIND_TEXT, text


class Finding:
    """Um achado: onde a busca encontrou o alvo e o que ela viu ali."""

    __slots__ = ("source", "interface", "summary", "detail")

    def __init__(self, source: str, interface: str, summary: str,
                 detail: dict | None = None):
        self.source = source
        self.interface = interface
        self.summary = summary
        self.detail = detail or {}

    def __repr__(self) -> str:            # pragma: no cover — depuração
        return f"<Finding {self.source} {self.interface} {self.summary!r}>"


def _safe(run, command: str, timeout: float = 30.0) -> str:
    """Executa tolerando comando inexistente na plataforma."""
    try:
        return run(command, timeout) or ""
    except Exception:                      # noqa: BLE001 — busca não quebra
        return ""


def search(run, query: str, descriptions: dict[str, str] | None = None) -> dict:
    """Procura ``query`` no equipamento.

    ``run(comando, timeout)`` executa e devolve a saída.
    ``descriptions`` é o mapa ``{interface: descrição}`` já conhecido pelo
    aplicativo — evita rebaixar um ``display interface description`` toda
    vez que alguém pesquisa algo.
    """
    kind, value = classify(query)
    findings: list[Finding] = []
    steps: list[str] = []
    if not value:
        return {"kind": kind, "query": value, "findings": [], "steps": [],
                "interfaces": [], "mac": "", "ips": []}

    macs: set[str] = set()
    ips: set[str] = set()
    if kind == KIND_MAC:
        macs.add(value)
    elif kind in (KIND_IPV4, KIND_IPV6):
        ips.add(value)

    # ── 1. ARP: do IP para o MAC (e vice-versa) ──────────────────────── #
    if kind in (KIND_IPV4, KIND_MAC):
        steps.append(f"display arp | include {value}")
        for row in parsers.parse_arp(_safe(run, f"display arp | include {value}")):
            findings.append(Finding(
                "ARP", row["interface"],
                f"{row['ip']} ↔ {row['mac']}"
                + (f" (VLAN/porta {row['interface']})" if row["interface"]
                   else ""),
                row))
            macs.add(row["mac"])
            ips.add(row["ip"])

    # ── 2. Tabela MAC: do MAC para a porta física ────────────────────── #
    for mac in sorted(macs):
        steps.append(f"display mac-address {mac}")
        for row in parsers.parse_mac_table(
                _safe(run, f"display mac-address {mac}")):
            findings.append(Finding(
                "Tabela MAC", row["interface"],
                f"{row['mac']} aprendido em {row['interface']}"
                + (f", VLAN {row['vlan']}" if row["vlan"] else ""),
                row))

    # ── 3. Sessão de banda larga (BRAS/PPPoE) ────────────────────────── #
    bras_filter = value if kind != KIND_MAC else value
    steps.append(f"display access-user | include {bras_filter}")
    bras_text = _safe(run, f"display access-user | include {bras_filter}", 45.0)
    for row in parsers.parse_access_users(bras_text):
        findings.append(Finding(
            "PPPoE / BAS", row["interface"],
            f"{row['username'] or 'assinante'} · {row['ip']} · {row['mac']}",
            row))
        if row["mac"]:
            macs.add(row["mac"])
        if row["ip"]:
            ips.add(row["ip"])

    # ── 4. Rota que cobre o IP ───────────────────────────────────────── #
    for ip in sorted(ips):
        if ":" in ip:
            continue
        steps.append(f"display ip routing-table {ip}")
        for row in parsers.parse_routing_table(
                _safe(run, f"display ip routing-table {ip}")):
            findings.append(Finding(
                "Rota", row["interface"],
                f"{row['destination']} via {row['nexthop']} "
                f"({row['proto']}) → {row['interface']}", row))
            break            # a rota mais específica basta

    # ── 5. Descrição das portas (busca por texto) ────────────────────── #
    if kind == KIND_TEXT and descriptions:
        needle = value.lower()
        for name, description in sorted(descriptions.items()):
            if needle in (description or "").lower() or needle in name.lower():
                findings.append(Finding(
                    "Descrição da porta", name,
                    f"{name}: {description}", {"name": name,
                                               "description": description}))

    findings.sort(key=lambda f: SOURCE_WEIGHT.get(f.source, 5))
    interfaces = _rank_interfaces(findings)
    return {
        "kind": kind,
        "query": value,
        "findings": findings,
        "steps": steps,
        "interfaces": interfaces,
        "mac": sorted(macs)[0] if macs else "",
        "ips": sorted(ips),
    }


# Quanto cada fonte vale para responder "em que porta o cliente está".
# A tabela MAC aponta a porta física em que o dispositivo realmente se
# encontra; o ARP costuma apontar a interface lógica (Vlanif) e a rota, a
# de saída. Os achados são ordenados por isso, e não pela ordem em que as
# consultas rodaram — quem abre o painel quer a resposta na primeira linha.
SOURCE_WEIGHT = {"Tabela MAC": 0, "PPPoE / BAS": 1, "ARP": 2,
                 "Descrição da porta": 3, "Rota": 4}


def _rank_interfaces(findings: list[Finding]) -> list[str]:
    """Portas citadas, das mais específicas para as menos."""
    ordered: dict[str, int] = {}
    for finding in findings:
        name = finding.interface
        if not name or name == "—":
            continue
        rank = SOURCE_WEIGHT.get(finding.source, 5)
        if name not in ordered or rank < ordered[name]:
            ordered[name] = rank
    return [name for name, _rank in sorted(ordered.items(),
                                           key=lambda kv: (kv[1], kv[0]))]


def describe(result: dict) -> str:
    """Frase única resumindo o que a busca concluiu."""
    if not result["findings"]:
        return (f"Nada encontrado para “{result['query']}”. O dispositivo "
                f"pode estar em outro equipamento, ou a entrada já ter "
                f"expirado das tabelas.")
    parts = []
    if result["interfaces"]:
        parts.append(f"porta mais provável: {result['interfaces'][0]}")
    if result["mac"]:
        parts.append(f"MAC {result['mac']}")
    if result["ips"]:
        parts.append("IP " + ", ".join(result["ips"][:3]))
    return f"{len(result['findings'])} achado(s) — " + "  ·  ".join(parts)
