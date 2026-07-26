"""Fila de comandos com prioridade sobre a sessão SSH.

Por que isto existe
-------------------
O VRP aceita **um** comando por vez em cada shell SSH. Antes, cada painel
chamava ``session.run()`` direto e o acesso era serializado por um lock
comum: quem chegasse primeiro ficava com a sessão. Com várias janelas
abertas em auto-atualização (Interfaces a cada 5 s, BGP, o indicador de
CPU/Memória da barra, o gráfico de tráfego...) o resultado era o problema
relatado — um ``display interface`` de 40 segundos numa placa cheia
segurava a fila inteira e o gráfico "congelava" no meio do desenho.

Esta fila resolve isso com quatro mecanismos:

``prioridade``
    Um clique do usuário (``INTERACTIVE``) fura a fila das atualizações de
    fundo (``BACKGROUND``). O equipamento continua atendendo um comando
    por vez, mas a *ordem* passa a favorecer quem está esperando na tela.

``coalescência``
    Dois painéis pedindo ``display interface description`` ao mesmo tempo
    executam **um** comando e recebem os dois a mesma resposta.

``cache curto``
    Saídas de ``display`` ficam guardadas por alguns segundos. Um painel
    atualizando a cada 5 s e outro a cada 10 s dividem a mesma leitura em
    vez de dobrarem a carga.

``descarte de pedidos vencidos``
    Se uma atualização de fundo passou tempo demais na fila, ela é
    descartada em vez de executada — quando finalmente rodasse, o painel
    já teria pedido a próxima. É isso que impede o efeito bola de neve
    sob uso intenso.

O módulo não conhece Qt: é ``threading`` puro e pode ser testado sozinho.
"""

from __future__ import annotations

import heapq
import threading
import time

# Prioridades (menor = atendido antes).
INTERACTIVE = 0   # clique do usuário, envio de configuração, terminal
NORMAL = 1        # abrir um painel, botão Atualizar
BACKGROUND = 2    # auto-refresh, telemetria, indicadores da barra

_PRIORITY_NAMES = {INTERACTIVE: "interativo", NORMAL: "normal",
                   BACKGROUND: "fundo"}

# Teto de pedidos de fundo enfileirados. Acima disto os mais antigos são
# descartados — sob carga, o dado velho não interessa mais a ninguém.
_MAX_BACKGROUND = 24


class Dropped(Exception):
    """Pedido descartado (venceu na fila ou o painel foi fechado)."""


class _Task:
    __slots__ = ("fn", "priority", "seq", "key", "expires", "tag", "ttl",
                 "_event", "_result", "_error", "cancelled", "queued_at")

    def __init__(self, fn, priority: int, seq: int, key, expires, tag,
                 ttl: float = 0.0):
        self.fn = fn
        self.priority = priority
        self.seq = seq
        self.key = key
        self.expires = expires        # time.monotonic() limite, ou None
        self.tag = tag
        self.ttl = ttl
        self._event = threading.Event()
        self._result = None
        self._error: BaseException | None = None
        self.cancelled = False
        self.queued_at = time.monotonic()

    # A heap ordena por (prioridade, ordem de chegada).
    def __lt__(self, other: "_Task") -> bool:
        return (self.priority, self.seq) < (other.priority, other.seq)

    def set_result(self, value) -> None:
        self._result = value
        self._event.set()

    def set_error(self, exc: BaseException) -> None:
        self._error = exc
        self._event.set()

    def wait(self, timeout: float | None = None):
        if not self._event.wait(timeout):
            raise TimeoutError("A fila de comandos não respondeu a tempo.")
        if self._error is not None:
            raise self._error
        return self._result


class CommandScheduler:
    """Despacha trabalho para a sessão a partir de uma única thread."""

    def __init__(self, name: str = "vrp"):
        self._heap: list[_Task] = []
        self._seq = 0
        self._cv = threading.Condition()
        self._inflight: dict[object, _Task] = {}   # coalescência
        self._cache: dict[object, tuple[float, object]] = {}
        self._stop = False
        self._worker_ident: int | None = None
        self._current: _Task | None = None
        self._name = name
        self._thread = threading.Thread(
            target=self._loop, name=f"scheduler-{name}", daemon=True)
        # Métricas mostradas na barra de status.
        self.executed = 0
        self.dropped = 0
        self.cache_hits = 0
        self.coalesced = 0
        self.last_latency = 0.0
        self.last_wait = 0.0
        self._thread.start()

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        with self._cv:
            self._stop = True
            pending, self._heap = self._heap, []
            self._cv.notify_all()
        for task in pending:
            task.set_error(Dropped("Sessão encerrada."))

    @property
    def depth(self) -> int:
        with self._cv:
            return len(self._heap)

    def stats(self) -> dict:
        with self._cv:
            return {
                "depth": len(self._heap),
                "running": self._current is not None,
                "executed": self.executed,
                "dropped": self.dropped,
                "cache_hits": self.cache_hits,
                "coalesced": self.coalesced,
                "last_latency": self.last_latency,
                "last_wait": self.last_wait,
            }

    # ------------------------------------------------------------------ #
    def submit(self, fn, *, priority: int = NORMAL, key=None,
               ttl: float = 0.0, max_wait: float | None = None,
               tag=None) -> _Task:
        """Enfileira ``fn`` e devolve a tarefa (use ``.wait()``).

        ``key``      identidade lógica para coalescer/cachear (ex.: o comando).
        ``ttl``      segundos que o resultado fica em cache (0 = não cacheia).
        ``max_wait`` descarta o pedido se ele passar tanto tempo na fila.
        ``tag``      rótulo do dono, para ``cancel(tag)`` ao fechar o painel.
        """
        now = time.monotonic()
        with self._cv:
            if self._stop:
                raise Dropped("Sessão encerrada.")
            # 1) cache ainda válido?
            if key is not None and ttl > 0:
                hit = self._cache.get(key)
                if hit and hit[0] > now:
                    self.cache_hits += 1
                    task = _Task(fn, priority, 0, key, None, tag)
                    task.set_result(hit[1])
                    return task
            # 2) pedido idêntico já em voo/na fila? Compartilha o resultado.
            if key is not None:
                running = self._inflight.get(key)
                if running is not None and not running.cancelled:
                    self.coalesced += 1
                    # Um pedido interativo promove o que já está na fila,
                    # senão o clique herdaria a espera de um job de fundo.
                    if priority < running.priority:
                        running.priority = priority
                        heapq.heapify(self._heap)
                    return running

            self._seq += 1
            task = _Task(fn, priority, self._seq, key,
                         None if max_wait is None else now + max_wait, tag,
                         ttl)
            heapq.heappush(self._heap, task)
            if key is not None:
                self._inflight[key] = task
            self._trim_locked()
            self._cv.notify()
            return task

    def run(self, fn, *, priority: int = NORMAL, key=None, ttl: float = 0.0,
            max_wait: float | None = None, tag=None, wait: float | None = None):
        """Enfileira e espera o resultado (bloqueia a thread chamadora)."""
        # Chamada de dentro do próprio worker (ex.: um fn que faz outra
        # consulta): executa direto, senão a fila esperaria por si mesma.
        if threading.get_ident() == self._worker_ident:
            return fn()
        task = self.submit(fn, priority=priority, key=key, ttl=ttl,
                           max_wait=max_wait, tag=tag)
        return task.wait(wait)

    def cancel(self, tag) -> int:
        """Descarta os pedidos pendentes de um dono (painel fechado)."""
        if tag is None:
            return 0
        victims = []
        with self._cv:
            for task in self._heap:
                if task.tag == tag and task is not self._current:
                    task.cancelled = True
                    victims.append(task)
            if victims:
                self._heap = [t for t in self._heap if not t.cancelled]
                heapq.heapify(self._heap)
                for task in victims:
                    if self._inflight.get(task.key) is task:
                        self._inflight.pop(task.key, None)
                self.dropped += len(victims)
        for task in victims:
            task.set_error(Dropped("Pedido cancelado."))
        return len(victims)

    def invalidate(self, prefix: str | None = None) -> None:
        """Esvazia o cache — obrigatório após aplicar configuração."""
        with self._cv:
            if prefix is None:
                self._cache.clear()
            else:
                for key in [k for k in self._cache
                            if isinstance(k, str) and k.startswith(prefix)]:
                    self._cache.pop(key, None)

    # ------------------------------------------------------------------ #
    def _trim_locked(self) -> None:
        """Corta o excesso de pedidos de fundo (mantém os mais recentes)."""
        background = [t for t in self._heap if t.priority >= BACKGROUND]
        if len(background) <= _MAX_BACKGROUND:
            return
        background.sort(key=lambda t: t.seq)
        victims = background[:len(background) - _MAX_BACKGROUND]
        stale = set(id(t) for t in victims)
        self._heap = [t for t in self._heap if id(t) not in stale]
        heapq.heapify(self._heap)
        for task in victims:
            if self._inflight.get(task.key) is task:
                self._inflight.pop(task.key, None)
            task.cancelled = True
            self.dropped += 1
            task.set_error(Dropped("Fila de fundo cheia — pedido descartado."))

    def _loop(self) -> None:
        self._worker_ident = threading.get_ident()
        while True:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                task = heapq.heappop(self._heap)
                self._current = task
            try:
                self._execute(task)
            finally:
                with self._cv:
                    self._current = None
                    if self._inflight.get(task.key) is task:
                        self._inflight.pop(task.key, None)

    def _execute(self, task: _Task) -> None:
        if task.cancelled:
            return
        now = time.monotonic()
        if task.expires is not None and now > task.expires:
            # Venceu na fila: quem pediu já vai pedir de novo.
            with self._cv:
                self.dropped += 1
            task.set_error(Dropped(
                "Pedido de fundo descartado (equipamento ocupado)."))
            return
        # Outro pedido igual pode ter preenchido o cache enquanto esperava.
        if task.key is not None and task.ttl > 0:
            with self._cv:
                hit = self._cache.get(task.key)
                if hit and hit[0] > now:
                    self.cache_hits += 1
                    task.set_result(hit[1])
                    return
        started = time.monotonic()
        try:
            result = task.fn()
        except BaseException as exc:            # noqa: BLE001 — vai ao chamador
            with self._cv:
                self.last_latency = time.monotonic() - started
                self.last_wait = started - task.queued_at
            task.set_error(exc)
            return
        with self._cv:
            self.executed += 1
            self.last_latency = time.monotonic() - started
            self.last_wait = started - task.queued_at
            if task.key is not None and task.ttl > 0:
                self._cache[task.key] = (time.monotonic() + task.ttl, result)
        task.set_result(result)
