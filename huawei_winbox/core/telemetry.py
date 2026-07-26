"""Motor de telemetria — amostragem contínua do equipamento.

Antes, cada janela buscava seus próprios números: o gráfico consultava
``display interface <porta>`` (uma porta por vez!), a barra de ferramentas
pedia CPU e memória por conta própria e o painel de Interfaces baixava o
dump completo. Com tudo aberto ao mesmo tempo, o equipamento recebia
consultas repetidas e a fila engasgava — daí o gráfico "pular" e não dar
para acompanhar várias coisas de uma vez.

Aqui há **um** coletor para toda a aplicação. Ele roda numa thread
própria, coleta **todas** as interfaces por amostra e guarda tudo no
histórico da sessão. Quem quiser dados — gráfico, painel, barra de status
— lê o último retrato em memória, sem tocar no equipamento.

Duas fontes, escolhidas automaticamente:

``SNMP``  (preferida)
    Um GET-BULK traz os contadores de todas as portas em 2 ou 3 pacotes
    UDP. É barato o bastante para amostrar de segundo em segundo, e a
    taxa é calculada aqui (diferença de octetos ÷ tempo), o que dá o
    valor **instantâneo** de verdade.

``CLI``  (sem SNMP configurado)
    Um único ``display interface brief`` traz a utilização de todas as
    portas; a banda nominal, lida uma vez do dump completo, converte
    porcentagem em bits/s. Continua sendo um comando por amostra
    independente de quantas portas estejam sendo observadas.
"""

from __future__ import annotations

import threading
import time
import weakref

from . import parsers, snmp
from .scheduler import BACKGROUND, Dropped

SOURCE_AUTO = "auto"
SOURCE_SNMP = "snmp"
SOURCE_CLI = "cli"

SOURCE_LABELS = {
    SOURCE_AUTO: "Automático",
    SOURCE_SNMP: "SNMP",
    SOURCE_CLI: "CLI (SSH)",
}

# Metadados (nome/velocidade/estado) mudam pouco: relê a cada 2 min.
_META_REFRESH = 120.0
# Saúde (CPU/memória/temperatura) não precisa da cadência do tráfego.
_HEALTH_EVERY = 5.0
# Taxa acima disto só pode ser contador reiniciado — descarta a amostra.
_SANITY_BPS = 1.6e12          # 1,6 Tbps
# Falhas seguidas antes de trocar de fonte no modo automático.
_FAILS_BEFORE_FALLBACK = 2


class TelemetryEngine:
    """Coletor único de métricas em tempo real de uma sessão."""

    def __init__(self, session, history, interval: float = 2.0):
        self.session = session
        self.history = history
        self._interval = max(1.0, float(interval))
        self._source = SOURCE_AUTO
        self._active_source = ""
        self._community = "public"
        self._snmp_port = 161
        self._snmp_timeout = 1.5

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()

        self._client: snmp.SnmpV2cClient | None = None
        self._meta: dict[int, dict] = {}
        self._meta_at = 0.0
        # Qual fonte montou os metadados. Importa porque as chaves não são
        # comparáveis: o SNMP usa o ifIndex real do equipamento e a CLI usa
        # a ordem da listagem. Trocar de fonte sem refazer o mapa casaria
        # contador de uma porta com o nome de outra.
        self._meta_source = ""
        self._prev: dict[int, tuple[float, int, int]] = {}
        self._counter_width = 64
        self._speeds: dict[str, int] = {}
        self._speeds_at = 0.0
        self._snmp_fails = 0
        self._cli_fails = 0
        # Quem está olhando o tráfego agora (gráfico, painel...). Sem
        # ninguém, o coletor só acompanha CPU/memória — abrir a aplicação
        # não deve custar um dump de interfaces que ninguém pediu.
        self._consumers: set[str] = set()

        # Último retrato — lido pela interface sem travar nada demorado.
        self.generation = 0
        self.status = "parado"
        self.last_error = ""
        self.last_sample_at = 0.0
        self.last_duration = 0.0
        self.rates: dict[str, tuple[float, float]] = {}
        # Contadores de erro por porta. São acumulados desde o boot, então
        # o valor absoluto diz pouco — 5 milhões de erros de três anos atrás
        # é ruído. O que importa é quanto subiu na última janela, e por isso
        # os totais vão para o histórico: a diferença sai de lá.
        self.errors: dict[str, tuple[int, int]] = {}
        self.health: dict[str, float | None] = {
            "cpu": None, "memory": None, "temperature": None}
        self._health_at = 0.0
        self._errors_at = 0.0

    # ================================================================== #
    # Configuração
    # ================================================================== #
    @property
    def interval(self) -> float:
        return self._interval

    def set_interval(self, seconds: float) -> None:
        with self._lock:
            self._interval = max(1.0, float(seconds))
        self._wake.set()

    @property
    def source(self) -> str:
        return self._source

    @property
    def active_source(self) -> str:
        """Fonte realmente em uso ('snmp' ou 'cli') — o modo auto decide."""
        return self._active_source

    def set_source(self, source: str) -> None:
        with self._lock:
            self._source = source if source in SOURCE_LABELS else SOURCE_AUTO
            self._prev.clear()
            self._snmp_fails = self._cli_fails = 0
            self._active_source = ""
        self._wake.set()

    @property
    def community(self) -> str:
        return self._community

    @property
    def snmp_port(self) -> int:
        return self._snmp_port

    def configure_snmp(self, community: str, port: int = 161,
                       timeout: float = 1.5) -> None:
        with self._lock:
            self._community = community or "public"
            self._snmp_port = int(port or 161)
            self._snmp_timeout = float(timeout)
            self._client = None
            self._meta = {}
            self._meta_at = 0.0
            self._prev.clear()
            self._snmp_fails = 0
        self._wake.set()

    def snmp_client(self) -> snmp.SnmpV2cClient:
        with self._lock:
            if self._client is None:
                self._client = snmp.SnmpV2cClient(
                    self.session.host, community=self._community,
                    port=self._snmp_port, timeout=self._snmp_timeout,
                    retries=1)
            return self._client

    # ================================================================== #
    # Ciclo de vida
    # ================================================================== #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        self.status = "parado"
        client = self._client
        if client is not None:
            client.close()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poke(self) -> None:
        """Força uma amostra imediata (usado ao abrir um painel)."""
        self._wake.set()

    # -- quem quer as séries de tráfego -------------------------------- #
    def acquire_traffic(self, who: str) -> None:
        """Liga a amostragem de interfaces enquanto ``who`` estiver aberto."""
        with self._lock:
            first = not self._consumers
            self._consumers.add(who)
        if first:
            self._wake.set()

    def release_traffic(self, who: str) -> None:
        with self._lock:
            self._consumers.discard(who)

    @property
    def sampling_traffic(self) -> bool:
        with self._lock:
            return bool(self._consumers)

    # ================================================================== #
    # Retrato para a interface
    # ================================================================== #
    def snapshot(self) -> dict:
        """Cópia consistente do estado — barata, sem I/O."""
        with self._lock:
            return {
                "generation": self.generation,
                "status": self.status,
                "source": self._active_source or self._source,
                "requested_source": self._source,
                "interval": self._interval,
                "rates": dict(self.rates),
                "errors": dict(self.errors),
                "health": dict(self.health),
                "error": self.last_error,
                "at": self.last_sample_at,
                "duration": self.last_duration,
                "interfaces": len(self.rates),
            }

    def interface_names(self) -> list[str]:
        with self._lock:
            if self._meta:
                return sorted(m["name"] for m in self._meta.values()
                              if m.get("name"))
        return self.history.interfaces()

    def interface_meta(self) -> dict[str, dict]:
        """Metadados por NOME de interface (velocidade, estado, alias)."""
        with self._lock:
            return {m["name"]: dict(m) for m in self._meta.values()
                    if m.get("name")}

    def totals(self) -> tuple[float, float]:
        """Soma de RX e TX de todas as interfaces físicas amostradas."""
        with self._lock:
            rates = list(self.rates.items())
        rx = tx = 0.0
        for name, (r, t) in rates:
            # Sub-interfaces e agregados contariam o mesmo tráfego duas vezes.
            if "." in name or name.lower().startswith(("vlanif", "loopback",
                                                       "null", "tunnel")):
                continue
            rx += r
            tx += t
        return rx, tx

    # ================================================================== #
    # Laço de coleta
    # ================================================================== #
    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._tick()
            except Exception as exc:              # noqa: BLE001 — nunca morre
                with self._lock:
                    self.last_error = str(exc)
                    self.status = "erro"
            with self._lock:
                self.last_duration = time.monotonic() - started
                interval = self._interval
                idle = not self._consumers
            # Nunca amostra mais rápido do que a coleta anterior demorou:
            # sob equipamento lento, o intervalo se alarga sozinho em vez de
            # empilhar pedidos (era exatamente isso que travava tudo).
            delay = max(interval, self.last_duration * 1.5)
            if idle:
                delay = max(delay, _HEALTH_EVERY)
            self._wake.wait(delay)
            self._wake.clear()

    def _tick(self) -> None:
        with self._lock:
            requested = self._source
            snmp_fails = self._snmp_fails
            want_traffic = bool(self._consumers)
        use_snmp = (requested == SOURCE_SNMP
                    or (requested == SOURCE_AUTO
                        and snmp_fails < _FAILS_BEFORE_FALLBACK))
        if not want_traffic:
            self._tick_health_only(use_snmp)
            return
        if use_snmp:
            try:
                self._tick_snmp()
                with self._lock:
                    self._snmp_fails = 0
                    self._active_source = SOURCE_SNMP
                    self.status = "coletando"
                    self.last_error = ""
                return
            except Exception as exc:              # noqa: BLE001
                with self._lock:
                    self._snmp_fails += 1
                    self.last_error = f"SNMP: {exc}"
                if requested == SOURCE_SNMP:
                    with self._lock:
                        self.status = "erro"
                        self._active_source = SOURCE_SNMP
                    return
                # Modo automático: cai para CLI nesta mesma volta.
        try:
            self._tick_cli()
            with self._lock:
                self._cli_fails = 0
                self._active_source = SOURCE_CLI
                self.status = "coletando"
                if requested == SOURCE_AUTO and self._snmp_fails:
                    self.last_error = ("SNMP indisponível — usando CLI. "
                                       "Habilite o agente SNMP para o modo "
                                       "de tempo real.")
        except Dropped:
            # Pedido descartado porque a sessão estava ocupada: normal sob
            # carga, a próxima amostra pega o dado.
            with self._lock:
                self.status = "aguardando equipamento"
        except Exception as exc:                  # noqa: BLE001
            with self._lock:
                self._cli_fails += 1
                self.last_error = f"CLI: {exc}"
                self.status = "erro"
            if requested == SOURCE_AUTO and self._cli_fails >= 3:
                # CLI também falhando: dá outra chance ao SNMP.
                with self._lock:
                    self._snmp_fails = 0

    def _tick_health_only(self, use_snmp: bool) -> None:
        """Sem ninguém olhando o tráfego: acompanha só CPU/memória."""
        now = time.time()
        if use_snmp:
            try:
                self._sample_health_snmp(self.snmp_client(), now)
                with self._lock:
                    self._snmp_fails = 0
                    self._active_source = SOURCE_SNMP
                    self.status = "ocioso (só carga)"
                return
            except Exception as exc:              # noqa: BLE001
                with self._lock:
                    self._snmp_fails += 1
                    self.last_error = f"SNMP: {exc}"
        self._sample_health_cli(now)
        with self._lock:
            self._active_source = SOURCE_CLI
            self.status = "ocioso (só carga)"

    # -- SNMP ---------------------------------------------------------- #
    def _tick_snmp(self) -> None:
        client = self.snmp_client()
        now = time.time()
        with self._lock:
            need_meta = (not self._meta
                         or self._meta_source != SOURCE_SNMP
                         or now - self._meta_at > _META_REFRESH)
        if need_meta:
            meta = client.interface_metadata()
            if not meta:
                raise snmp.SnmpError(
                    "O agente não listou interfaces (ifName/ifDescr vazios).")
            with self._lock:
                self._meta = meta
                self._meta_at = now
                self._meta_source = SOURCE_SNMP
                self._prev.clear()      # índices novos: linha de base zerada

        counters, width = client.interface_counters()
        sample_at = time.time()
        with self._lock:
            meta = self._meta
            prev = self._prev
            self._counter_width = width
        wrap = 1 << width
        rates: dict[str, tuple[float, float]] = {}

        for index, (in_octets, out_octets) in counters.items():
            info = meta.get(index)
            name = info["name"] if info else f"ifIndex {index}"
            if not name:
                continue
            previous = prev.get(index)
            prev[index] = (sample_at, in_octets, out_octets)
            if previous is None:
                continue
            t_prev, in_prev, out_prev = previous
            dt = sample_at - t_prev
            if dt <= 0:
                continue
            rx = self._delta_bps(in_prev, in_octets, dt, wrap)
            tx = self._delta_bps(out_prev, out_octets, dt, wrap)
            if rx is None or tx is None:
                continue
            speed = (info or {}).get("speed_bps") or 0
            ceiling = speed * 1.25 if speed else _SANITY_BPS
            if rx > ceiling or tx > ceiling:
                # Reinício de contador ou reboot da placa: ignora a amostra
                # em vez de cravar um pico falso no gráfico.
                continue
            rates[name] = (rx, tx)
            self.history.add(name, rx, tx, when=sample_at)

        with self._lock:
            self.rates = rates
            self.last_sample_at = sample_at
            self.generation += 1

        self._sample_health_snmp(client, sample_at)
        self._sample_errors_snmp(client, sample_at)

    def _sample_errors_snmp(self, client, when: float) -> None:
        """Erros por porta via IF-MIB, na cadência lenta da saúde."""
        with self._lock:
            due = when - self._errors_at >= _HEALTH_EVERY
            meta = self._meta
        if not due:
            return
        with self._lock:
            self._errors_at = when
        try:
            per_index = client.interface_errors()
        except snmp.SnmpError:
            return
        counters = {}
        for index, values in per_index.items():
            info = meta.get(index)
            if not info or not info.get("name"):
                continue
            counters[info["name"]] = (values["in_errors"] + values["in_discards"],
                                      values["out_errors"] + values["out_discards"])
        if counters:
            self._record_errors(counters, when)

    @staticmethod
    def _delta_bps(previous: int, current: int, dt: float,
                   wrap: int) -> float | None:
        delta = current - previous
        if delta < 0:
            delta += wrap                  # contador deu a volta
            if delta < 0 or delta > wrap:
                return None
        return delta * 8.0 / dt

    def _sample_health_snmp(self, client, when: float) -> None:
        with self._lock:
            due = when - self._health_at >= _HEALTH_EVERY
        if not due:
            return
        try:
            data = client.system_health()
        except snmp.SnmpError:
            # Muitos equipamentos não expõem a MIB Huawei de entidade;
            # nesse caso a saúde vem pela CLI, com cadência lenta.
            self._sample_health_cli(when)
            return
        with self._lock:
            self._health_at = when
            self.health = {"cpu": data.get("cpu"),
                           "memory": data.get("memory"),
                           "temperature": data.get("temperature")}
        for key in ("cpu", "memory", "temperature"):
            value = data.get(key)
            if value is not None:
                self.history.add_metric(key, float(value), when=when)

    # -- CLI ----------------------------------------------------------- #
    def _tick_cli(self) -> None:
        now = time.time()
        with self._lock:
            need_speeds = (not self._speeds
                           or now - self._speeds_at > _META_REFRESH * 4)
        if need_speeds:
            # Dump completo: caro, mas só de tempos em tempos — é dele que
            # saem as bandas nominais usadas para converter a utilização.
            text = self.session.run(
                "display interface", timeout=90.0, priority=BACKGROUND,
                ttl=30.0, max_wait=120.0, tag="telemetry")
            speeds = parsers.parse_interface_bandwidth_bps(text)
            rates_now = parsers.parse_interface_rates_bps(text)
            with self._lock:
                self._speeds = speeds
                self._speeds_at = now
            # O dump já traz o estado de cada porta: aproveita antes de sair
            # por este caminho, senão o Painel ficaria sem os metadados até
            # o próximo ciclo.
            self._absorb_states(parsers.parse_interface_states(text))
            if rates_now:
                self._store_cli_rates(
                    {n: (float(r), float(t)) for n, (r, t) in rates_now.items()},
                    time.time())
                return

        brief = self.session.run(
            "display interface brief", timeout=30.0, priority=BACKGROUND,
            ttl=max(1.0, self._interval * 0.6), max_wait=self._interval * 4,
            tag="telemetry")
        # O "brief" já traz o estado de cada porta; sem aproveitá-lo, o modo
        # CLI ficava sem metadados nenhum e o Painel não sabia dizer quantas
        # interfaces estavam ativas nem a tabela do gráfico mostrava o estado.
        self._absorb_brief_meta(brief)
        utilization = parsers.parse_interface_utilization(brief)
        with self._lock:
            speeds = dict(self._speeds)
        rates: dict[str, tuple[float, float]] = {}
        for name, (in_pct, out_pct) in utilization.items():
            speed = speeds.get(name)
            if not speed:
                continue
            rates[name] = (in_pct / 100.0 * speed, out_pct / 100.0 * speed)
        if not rates:
            # Sem coluna de utilização (ou sem banda conhecida): volta ao
            # dump completo, que traz a taxa já em bits/s.
            text = self.session.run(
                "display interface", timeout=90.0, priority=BACKGROUND,
                ttl=max(2.0, self._interval * 0.6),
                max_wait=self._interval * 6, tag="telemetry")
            rates = {n: (float(r), float(t))
                     for n, (r, t) in parsers.parse_interface_rates_bps(text).items()}
            with self._lock:
                self._speeds.update(
                    parsers.parse_interface_bandwidth_bps(text))
        self._store_cli_rates(rates, time.time())
        self._sample_health_cli(time.time())

    def _absorb_brief_meta(self, brief: str) -> None:
        """Monta metadados e contadores de erro a partir do ``brief``."""
        rows = parsers.parse_interface_brief(brief)
        self._absorb_states({row["name"]: (row["phy"], row["protocol"])
                             for row in rows})
        counters = {row["name"]: (row["in_errors"], row["out_errors"])
                    for row in rows
                    if row["in_errors"] is not None
                    and row["out_errors"] is not None}
        if counters:
            self._record_errors(counters, time.time())

    def _record_errors(self, counters: dict[str, tuple[int, int]],
                       when: float) -> None:
        """Guarda os contadores de erro no histórico.

        Guarda o **total**, não a diferença: assim qualquer janela de tempo
        pode ser calculada depois (a diferença entre a primeira e a última
        amostra da janela), sem prender o dado à cadência da coleta.
        """
        for name, (in_errors, out_errors) in counters.items():
            self.history.add_metric(f"errin:{name}", in_errors, when=when)
            self.history.add_metric(f"errout:{name}", out_errors, when=when)
        with self._lock:
            self.errors = dict(counters)

    def _absorb_states(self, states: dict[str, tuple[str, str]]) -> None:
        """Guarda nome/estado/velocidade das portas vindos da CLI.

        As chaves são um índice sequencial, não o ifIndex do equipamento —
        por isso ``_meta_source`` marca a origem: trocar para SNMP obriga a
        refazer o mapa antes de casar contadores com nomes.
        """
        if not states:
            return
        with self._lock:
            speeds = dict(self._speeds)
        meta = {}
        for index, (name, (phy, _protocol)) in enumerate(sorted(states.items()),
                                                         start=1):
            normalized = phy.lower().lstrip("*^")
            meta[index] = {
                "name": name,
                "alias": "",
                "speed_bps": speeds.get(name, 0),
                "oper": "up" if normalized.startswith("up") else "down",
                "admin": "down" if phy.startswith("*") else "up",
            }
        with self._lock:
            self._meta = meta
            self._meta_at = time.time()
            self._meta_source = SOURCE_CLI

    def _store_cli_rates(self, rates: dict[str, tuple[float, float]],
                         when: float) -> None:
        for name, (rx, tx) in rates.items():
            self.history.add(name, rx, tx, when=when)
        with self._lock:
            self.rates = rates
            self.last_sample_at = when
            self.generation += 1

    def _sample_health_cli(self, when: float) -> None:
        with self._lock:
            due = when - self._health_at >= _HEALTH_EVERY
        if not due:
            return
        with self._lock:
            self._health_at = when       # marca antes: evita rajada em erro
        try:
            cpu_text = self.session.run(
                "display cpu-usage", timeout=10.0, priority=BACKGROUND,
                ttl=_HEALTH_EVERY * 0.8, max_wait=20.0, tag="telemetry")
            mem_text = self.session.run(
                "display memory-usage", timeout=10.0, priority=BACKGROUND,
                ttl=_HEALTH_EVERY * 0.8, max_wait=20.0, tag="telemetry")
        except Exception:                         # noqa: BLE001 — próxima volta
            return
        cpu = _percent(parsers.parse_cpu_usage(cpu_text))
        mem = _percent(parsers.parse_memory_usage(mem_text))
        with self._lock:
            self.health["cpu"] = cpu
            self.health["memory"] = mem
        if cpu is not None:
            self.history.add_metric("cpu", cpu, when=when)
        if mem is not None:
            self.history.add_metric("memory", mem, when=when)


def _percent(text: str) -> float | None:
    try:
        return float(str(text).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


_ENGINES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_ENGINES_LOCK = threading.Lock()


def for_session(session, history=None) -> TelemetryEngine:
    """Motor único por sessão (criado na primeira chamada)."""
    from . import history as history_mod
    with _ENGINES_LOCK:
        engine = _ENGINES.get(session)
        if engine is None:
            engine = TelemetryEngine(
                session, history or history_mod.for_session(session))
            _ENGINES[session] = engine
        return engine


def release(session) -> None:
    """Encerra e esquece o motor de uma sessão (ao desconectar)."""
    with _ENGINES_LOCK:
        engine = _ENGINES.pop(session, None)
    if engine is not None:
        engine.stop()
