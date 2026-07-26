"""Histórico de telemetria em memória, por sessão.

Guarda as séries coletadas enquanto o aplicativo está aberto — o
"histórico do tempo que o sistema está aberto puxando dados" que os
gráficos exibem. O armazenamento é vinculado à ``HuaweiSession`` (via
``WeakKeyDictionary``), então os dados sobrevivem ao fechar/reabrir os
painéis mas são liberados quando a conexão termina. Nada vai para disco.

Duas famílias de séries convivem aqui:

* **tráfego** — ``(timestamp, rx_bps, tx_bps)`` por interface;
* **métricas** — ``(timestamp, valor)`` para CPU, memória e temperatura.

O armazenamento usa ``array('d')`` em anel em vez de listas de tuplas.
Com amostragem de 1 s e algumas dezenas de portas, a diferença é entre
alguns megabytes e algumas dezenas — o formato compacto é o que torna
viável acompanhar **todas** as interfaces ao mesmo tempo.
"""

from __future__ import annotations

import time
import weakref
from array import array

# 5400 amostras por série: 90 min a 1 s, 7h30 a 5 s. Cada interface ocupa
# ~130 KB (3 arrays de doubles), então 50 portas cabem em ~6 MB.
_DEFAULT_CAP = 5400


class Series:
    """Buffer circular de amostras ``(t, a, b)`` com armazenamento compacto."""

    __slots__ = ("cap", "_t", "_a", "_b", "_n", "_head")

    def __init__(self, cap: int = _DEFAULT_CAP):
        self.cap = max(2, int(cap))
        self._t = array("d", bytes(8 * self.cap))
        self._a = array("d", bytes(8 * self.cap))
        self._b = array("d", bytes(8 * self.cap))
        self._n = 0        # quantas amostras válidas existem
        self._head = 0     # próxima posição de escrita

    def __len__(self) -> int:
        return self._n

    def add(self, t: float, a: float, b: float = 0.0) -> None:
        i = self._head
        self._t[i] = t
        self._a[i] = a
        self._b[i] = b
        self._head = (i + 1) % self.cap
        if self._n < self.cap:
            self._n += 1

    def _index(self, k: int) -> int:
        """Posição física da k-ésima amostra mais antiga."""
        return (self._head - self._n + k) % self.cap

    def latest(self) -> tuple[float, float, float] | None:
        if not self._n:
            return None
        i = (self._head - 1) % self.cap
        return (self._t[i], self._a[i], self._b[i])

    def first_time(self) -> float:
        return self._t[self._index(0)] if self._n else 0.0

    def _search(self, since: float) -> int:
        """Índice lógico da primeira amostra >= ``since`` (busca binária)."""
        lo, hi = 0, self._n
        while lo < hi:
            mid = (lo + hi) // 2
            if self._t[self._index(mid)] < since:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def window(self, since: float | None = None,
               max_points: int | None = None) -> list[tuple[float, float, float]]:
        """Amostras a partir de ``since``, reduzidas a ``max_points``.

        A redução é por **máximo do balde**, não por salto: cada ponto
        devolvido carrega o maior RX e o maior TX do intervalo que ele
        representa. Num gráfico de banda isso importa — um pico de 900 Mbps
        que durasse dois segundos sumiria com amostragem por salto, e é
        justamente o pico que se quer enxergar. Também é o que mantém o
        desenho leve: nunca se desenha mais pontos do que há pixels.
        """
        n = self._n
        if not n:
            return []
        start = 0
        if since is not None:
            start = self._search(since)
            if start >= n:
                start = max(0, n - 2)      # sempre devolve algo desenhável
        count = n - start
        if not max_points or count <= max_points:
            index = self._index
            return [(self._t[i], self._a[i], self._b[i])
                    for i in (index(k) for k in range(start, n))]

        step = (count + max_points - 1) // max_points
        out = []
        index = self._index
        for bucket_start in range(start, n, step):
            bucket_end = min(bucket_start + step, n)
            i = index(bucket_start)
            peak_a = self._a[i]
            peak_b = self._b[i]
            for k in range(bucket_start + 1, bucket_end):
                j = index(k)
                if self._a[j] > peak_a:
                    peak_a = self._a[j]
                if self._b[j] > peak_b:
                    peak_b = self._b[j]
            # Timestamp do meio do balde: mantém o eixo de tempo honesto.
            middle = index((bucket_start + bucket_end - 1) // 2)
            out.append((self._t[middle], peak_a, peak_b))
        # A amostra mais recente é o valor "agora" da legenda: sempre entra.
        last = index(n - 1)
        if out and out[-1][0] != self._t[last]:
            out.append((self._t[last], self._a[last], self._b[last]))
        return out

    def peak(self, since: float | None = None) -> tuple[float, float]:
        """Máximos de ``a`` e ``b`` na janela."""
        peak_a = peak_b = 0.0
        for _t, a, b in self.window(since):
            peak_a = max(peak_a, a)
            peak_b = max(peak_b, b)
        return peak_a, peak_b

    def average(self, since: float | None = None) -> tuple[float, float]:
        rows = self.window(since)
        if not rows:
            return 0.0, 0.0
        return (sum(r[1] for r in rows) / len(rows),
                sum(r[2] for r in rows) / len(rows))


class TrafficHistory:
    """Séries de tráfego por interface + métricas do equipamento."""

    def __init__(self, cap: int = _DEFAULT_CAP):
        self.cap = cap
        self.started = time.time()
        self._series: dict[str, Series] = {}
        self._metrics: dict[str, Series] = {}

    # -- tráfego ------------------------------------------------------- #
    def add(self, iface: str, rx_bps: float, tx_bps: float,
            when: float | None = None) -> None:
        t = time.time() if when is None else when
        series = self._series.get(iface)
        if series is None:
            series = Series(self.cap)
            self._series[iface] = series
        series.add(t, float(rx_bps), float(tx_bps))

    def series(self, iface: str) -> Series | None:
        return self._series.get(iface)

    def samples(self, iface: str, since: float | None = None,
                max_points: int | None = None) -> list[tuple[float, float, float]]:
        series = self._series.get(iface)
        return series.window(since, max_points) if series else []

    def last(self, iface: str) -> tuple[float, float, float] | None:
        series = self._series.get(iface)
        return series.latest() if series else None

    def interfaces(self) -> list[str]:
        return sorted(self._series)

    def active_interfaces(self, minimum_bps: float = 1.0) -> list[str]:
        """Interfaces que registraram tráfego — atalho para "mostre o que importa"."""
        out = []
        for name, series in self._series.items():
            last = series.latest()
            if last and (last[1] >= minimum_bps or last[2] >= minimum_bps):
                out.append(name)
        return sorted(out)

    # -- métricas (CPU, memória, temperatura) --------------------------- #
    def add_metric(self, name: str, value: float,
                   when: float | None = None) -> None:
        t = time.time() if when is None else when
        series = self._metrics.get(name)
        if series is None:
            series = Series(self.cap)
            self._metrics[name] = series
        series.add(t, float(value))

    def metric(self, name: str) -> Series | None:
        return self._metrics.get(name)

    def metric_samples(self, name: str, since: float | None = None,
                       max_points: int | None = None) -> list[tuple[float, float, float]]:
        series = self._metrics.get(name)
        return series.window(since, max_points) if series else []

    def metric_names(self) -> list[str]:
        return sorted(self._metrics)

    # -- manutenção ---------------------------------------------------- #
    def clear(self, iface: str | None = None) -> None:
        if iface is None:
            self._series.clear()
            self._metrics.clear()
        else:
            self._series.pop(iface, None)

    def memory_estimate(self) -> int:
        """Bytes aproximados ocupados pelas séries (mostrado no Painel)."""
        per_series = self.cap * 8 * 3
        return (len(self._series) + len(self._metrics)) * per_series


_STORE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def for_session(session) -> TrafficHistory:
    """Retorna (criando se preciso) o histórico associado a uma sessão."""
    history = _STORE.get(session)
    if history is None:
        history = TrafficHistory()
        _STORE[session] = history
    return history
