"""Verificação de saúde — uma lista de conferências sobre o equipamento.

O aplicativo já sabe ler alarmes, óptica, erros de porta, BGP, OSPF e a
diferença entre a configuração em execução e a salva. O que faltava era
juntar tudo numa resposta única à pergunta que se faz ao assumir um
plantão ou ao terminar uma manutenção: **este equipamento está bem?**

Cada conferência devolve um veredito (``ok``, ``atenção``, ``falha`` ou
``n/d``), o que foi observado e o que fazer a respeito. Nada aqui consulta
o equipamento: o módulo recebe os dados já coletados, o que o torna
testável com cenários fabricados — inclusive os ruins, que são
justamente os difíceis de reproduzir num laboratório.

``n/d`` é um veredito de primeira classe, e não um "ok" disfarçado: se o
equipamento não expõe o dado, dizer que está tudo bem seria mentira.
"""

from __future__ import annotations

OK = "ok"
WARN = "atenção"
FAIL = "falha"
UNKNOWN = "n/d"

_RANK = {FAIL: 0, WARN: 1, UNKNOWN: 2, OK: 3}

# Limiares das conferências de carga.
CPU_WARN, CPU_FAIL = 70.0, 90.0
MEMORY_WARN, MEMORY_FAIL = 75.0, 90.0
# Placa de rede trabalha quente: 40–65 °C é rotina e o alarme do próprio
# equipamento costuma ficar em 80–90 °C. Marcar falha em 75 encheria a
# lista de alarme falso, que é o jeito mais rápido de fazer alguém parar
# de olhar a verificação.
TEMPERATURE_WARN, TEMPERATURE_FAIL = 68.0, 82.0
# Abaixo disto, o equipamento reiniciou há pouco e vale avisar.
RECENT_BOOT_HOURS = 24


class Check:
    """Resultado de uma conferência."""

    __slots__ = ("key", "title", "status", "detail", "hint", "panel")

    def __init__(self, key: str, title: str, status: str, detail: str = "",
                 hint: str = "", panel: str = ""):
        self.key = key
        self.title = title
        self.status = status
        self.detail = detail
        self.hint = hint
        self.panel = panel

    def __repr__(self) -> str:            # pragma: no cover — depuração
        return f"<Check {self.key} {self.status}>"


def _level(value, warn: float, fail: float) -> str:
    if value is None:
        return UNKNOWN
    if value >= fail:
        return FAIL
    if value >= warn:
        return WARN
    return OK


# ── conferências ─────────────────────────────────────────────────────── #
def _check_unsaved(data: dict) -> Check:
    unsaved = data.get("unsaved_lines")
    if unsaved is None:
        return Check("config", "Configuração gravada na flash", UNKNOWN,
                     "não foi possível comparar com a configuração salva",
                     "Abra Configuração → Comparar com a salva.",
                     "Configuração")
    if unsaved:
        return Check(
            "config", "Configuração gravada na flash", FAIL,
            f"{unsaved} linha(s) em execução ainda não foram salvas",
            "Um reboot agora perderia essas alterações. Use Sessão → "
            "Salvar configuração.", "Configuração")
    return Check("config", "Configuração gravada na flash", OK,
                 "o que está em execução é o que está na flash")


def _check_alarms(data: dict) -> Check:
    alarms = data.get("alarms")
    if alarms is None:
        return Check("alarmes", "Alarmes ativos", UNKNOWN,
                     "este equipamento não expõe 'display alarm active'",
                     "Confira o painel de Logs.", "Logs")
    severe = [a for a in alarms if a.get("level", 9) <= 2]
    minor = [a for a in alarms if 2 < a.get("level", 9) <= 4]
    if severe:
        return Check(
            "alarmes", "Alarmes ativos", FAIL,
            f"{len(severe)} alarme(s) crítico/maior: "
            + "; ".join(a.get("description", "")[:60] for a in severe[:3]),
            "Trate os alarmes antes de qualquer outra coisa.", "Alarmes")
    if minor:
        return Check("alarmes", "Alarmes ativos", WARN,
                     f"{len(minor)} alarme(s) de menor gravidade",
                     "Vale olhar, mas não é urgente.", "Alarmes")
    return Check("alarmes", "Alarmes ativos", OK, "nenhum alarme ativo")


def _check_cpu(data: dict) -> Check:
    value = (data.get("health") or {}).get("cpu")
    status = _level(value, CPU_WARN, CPU_FAIL)
    detail = f"{value:.0f}%" if value is not None else "sem leitura"
    hint = ("Veja quais processos consomem com 'display cpu-usage'."
            if status in (WARN, FAIL) else "")
    return Check("cpu", "Uso de CPU", status, detail, hint, "Hardware")


def _check_memory(data: dict) -> Check:
    value = (data.get("health") or {}).get("memory")
    status = _level(value, MEMORY_WARN, MEMORY_FAIL)
    detail = f"{value:.0f}%" if value is not None else "sem leitura"
    hint = ("Memória alta costuma preceder instabilidade; observe a "
            "tendência no Painel." if status in (WARN, FAIL) else "")
    return Check("memoria", "Uso de memória", status, detail, hint, "Painel")


def _check_temperature(data: dict) -> Check:
    value = (data.get("health") or {}).get("temperature")
    status = _level(value, TEMPERATURE_WARN, TEMPERATURE_FAIL)
    detail = f"{value:.0f} °C" if value is not None else "sem leitura"
    hint = ("Verifique ventilação e filtros." if status in (WARN, FAIL)
            else "")
    return Check("temperatura", "Temperatura", status, detail, hint,
                 "Hardware")


def _check_optics(data: dict) -> Check:
    optics = data.get("optics")
    if not optics:
        return Check("optica", "Potência óptica das portas", UNKNOWN,
                     "nenhum transceptor com diagnóstico disponível", "",
                     "Saúde das portas")
    critical = [e for e in optics.values() if e.get("status") == "crítico"]
    warning = [e for e in optics.values() if e.get("status") == "atenção"]
    unknown = [e for e in optics.values() if e.get("status") == "sem limiar"]
    if critical:
        return Check(
            "optica", "Potência óptica das portas", FAIL,
            f"{len(critical)} porta(s) fora do limiar: "
            + ", ".join(e["name"] for e in critical[:4]),
            "Verifique conectores, emendas e o orçamento óptico do enlace.",
            "Saúde das portas")
    if warning:
        return Check(
            "optica", "Potência óptica das portas", WARN,
            f"{len(warning)} porta(s) com pouca folga: "
            + ", ".join(f"{e['name']} ({e['margin_db']:+.1f} dB)"
                        for e in warning[:4]),
            "Enlaces com menos de 3 dB de folga costumam cair na primeira "
            "intempérie.", "Saúde das portas")
    if unknown and len(unknown) == len(optics):
        return Check("optica", "Potência óptica das portas", UNKNOWN,
                     "limiares dos módulos ainda não lidos",
                     "Abra Saúde das portas para que sejam consultados.",
                     "Saúde das portas")
    return Check("optica", "Potência óptica das portas", OK,
                 f"{len(optics)} porta(s) dentro dos limiares")


def _check_errors(data: dict) -> Check:
    growing = data.get("growing_errors")
    if growing is None:
        return Check("erros", "Erros nas interfaces", UNKNOWN,
                     "contadores de erro não disponíveis", "",
                     "Saúde das portas")
    if not growing:
        return Check("erros", "Erros nas interfaces", OK,
                     "nenhum contador subindo na janela observada")
    listed = ", ".join(f"{name} (+{count})" for name, count in growing[:4])
    return Check(
        "erros", "Erros nas interfaces", WARN,
        f"{len(growing)} porta(s) com erro crescendo: {listed}",
        "Erro que sobe agora costuma ser cabo, conector ou módulo — "
        "não o acumulado histórico.", "Saúde das portas")


def _check_bgp(data: dict) -> Check:
    peers = data.get("bgp")
    if not peers:
        return Check("bgp", "Vizinhos BGP", UNKNOWN, "sem BGP configurado")
    down = [p for p in peers
            if not p.get("state", "").lower().startswith("estab")]
    if down:
        return Check(
            "bgp", "Vizinhos BGP", FAIL,
            f"{len(down)} de {len(peers)} vizinho(s) fora de Established: "
            + ", ".join(f"{p['peer']} ({p['state']})" for p in down[:4]),
            "Confira a sessão, a ACL do peer e o alcance do next-hop.",
            "BGP")
    return Check("bgp", "Vizinhos BGP", OK,
                 f"{len(peers)} vizinho(s), todos estabelecidos")


def _check_ospf(data: dict) -> Check:
    peers = data.get("ospf")
    if not peers:
        return Check("ospf", "Vizinhanças OSPF", UNKNOWN,
                     "sem OSPF configurado")
    down = [p for p in peers
            if not p.get("state", "").lower().startswith("full")]
    if down:
        return Check(
            "ospf", "Vizinhanças OSPF", WARN,
            f"{len(down)} vizinho(s) fora de Full: "
            + ", ".join(f"{p['neighbor']} ({p['state']})" for p in down[:4]),
            "Em rede com DR/BDR, 2-Way entre DROthers é normal.", "OSPF")
    return Check("ospf", "Vizinhanças OSPF", OK,
                 f"{len(peers)} vizinhança(s), todas em Full")


def _check_ntp(data: dict) -> Check:
    status = (data.get("ntp") or "").strip().lower()
    if not status or status == "—":
        return Check("ntp", "Relógio sincronizado (NTP)", UNKNOWN,
                     "sem informação de NTP", "", "Serviços")
    if status.startswith("synchronized"):
        return Check("ntp", "Relógio sincronizado (NTP)", OK, status)
    return Check(
        "ntp", "Relógio sincronizado (NTP)", WARN, status,
        "Sem relógio certo, o horário dos logs e dos alarmes não bate com "
        "o de outros sistemas.", "Serviços")


def _check_down_with_description(data: dict) -> Check:
    interfaces = data.get("interfaces")
    if not interfaces:
        return Check("portas", "Portas descritas fora do ar", UNKNOWN,
                     "lista de interfaces indisponível", "", "Interfaces")
    down = []
    for row in interfaces:
        phy = str(row.get("phy", "")).lower()
        description = (row.get("description") or "").strip()
        # Porta sem descrição e fora do ar quase sempre é porta livre; com
        # descrição, alguém escreveu ali porque ela serve para alguma coisa.
        if description and description != "-" and phy.startswith("down"):
            down.append(f"{row['name']} ({description[:28]})")
    if down:
        return Check("portas", "Portas descritas fora do ar", WARN,
                     f"{len(down)}: " + ", ".join(down[:4]),
                     "Portas com descrição costumam ser links em uso.",
                     "Interfaces")
    return Check("portas", "Portas descritas fora do ar", OK,
                 "nenhuma porta descrita está fora do ar")


def _check_security(data: dict) -> Check:
    problems = []
    if data.get("telnet_enabled"):
        problems.append("Telnet habilitado (tráfego em texto claro)")
    communities = data.get("snmp_communities") or []
    weak = [c for c in communities
            if str(c.get("name", "")).lower() in ("public", "private")]
    if weak:
        problems.append("comunidade SNMP padrão em uso "
                        f"({', '.join(c['name'] for c in weak)})")
    writable = [c for c in communities
                if "write" in str(c.get("access", "")).lower()]
    if writable:
        problems.append(f"{len(writable)} comunidade(s) SNMP com escrita")
    if not problems:
        if not communities and "telnet_enabled" not in data:
            return Check("seguranca", "Higiene de acesso", UNKNOWN,
                         "sem informação sobre Telnet e SNMP", "", "Serviços")
        return Check("seguranca", "Higiene de acesso", OK,
                     "sem Telnet nem comunidade SNMP padrão")
    return Check("seguranca", "Higiene de acesso", WARN, "; ".join(problems),
                 "São os pontos que uma auditoria aponta primeiro.",
                 "Serviços")


def _check_uptime(data: dict) -> Check:
    hours = data.get("uptime_hours")
    if hours is None:
        return Check("uptime", "Tempo ligado", UNKNOWN, "sem leitura")
    if hours < RECENT_BOOT_HOURS:
        return Check(
            "uptime", "Tempo ligado", WARN,
            f"reiniciou há {hours:.0f} h",
            "Reboot recente — se não foi planejado, procure a causa nos "
            "logs.", "Logs")
    days = hours / 24
    return Check("uptime", "Tempo ligado", OK, f"{days:.0f} dia(s) no ar")


_CHECKS = (
    _check_unsaved, _check_alarms, _check_optics, _check_errors,
    _check_bgp, _check_ospf, _check_cpu, _check_memory, _check_temperature,
    _check_down_with_description, _check_ntp, _check_security, _check_uptime,
)


def run(data: dict) -> list[Check]:
    """Roda todas as conferências, das mais graves para as menos."""
    results = []
    for check in _CHECKS:
        try:
            results.append(check(data))
        except Exception as exc:           # noqa: BLE001 — uma não derruba as outras
            results.append(Check(getattr(check, "__name__", "?"),
                                 "Conferência interrompida", UNKNOWN,
                                 f"erro ao avaliar: {exc}"))
    results.sort(key=lambda c: (_RANK.get(c.status, 9), c.title))
    return results


def summarize(results: list[Check]) -> tuple[str, str]:
    """(veredito geral, frase) a partir das conferências."""
    counts = {}
    for check in results:
        counts[check.status] = counts.get(check.status, 0) + 1
    if counts.get(FAIL):
        verdict = FAIL
        phrase = (f"{counts[FAIL]} problema(s) que pedem ação agora")
    elif counts.get(WARN):
        verdict = WARN
        phrase = f"{counts[WARN]} ponto(s) de atenção"
    elif counts.get(OK):
        verdict = OK
        phrase = "nada aponta problema"
    else:
        verdict = UNKNOWN
        phrase = "não foi possível avaliar"
    extras = []
    for status in (OK, WARN, FAIL, UNKNOWN):
        if counts.get(status):
            extras.append(f"{counts[status]} {status}")
    return verdict, f"{phrase}  ·  " + "  ·  ".join(extras)


def parse_uptime_hours(text: str) -> float | None:
    """'214 days, 3 hours, 11 minutes' -> horas."""
    if not text:
        return None
    import re
    total = 0.0
    found = False
    for value, unit in re.findall(r"(\d+)\s*(week|day|hour|minute)", text,
                                  re.IGNORECASE):
        found = True
        factor = {"week": 168.0, "day": 24.0, "hour": 1.0,
                  "minute": 1 / 60}[unit.lower()]
        total += int(value) * factor
    return total if found else None
