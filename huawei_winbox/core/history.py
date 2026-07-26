"""Histórico de tráfego em memória, por sessão.

Guarda as amostras de banda (RX/TX em bits/s) coletadas enquanto o
aplicativo está aberto — exatamente o "histórico do tempo que o sistema
está aberto puxando dados" que o gráfico exibe. O armazenamento é
vinculado à ``HuaweiSession`` (via ``WeakKeyDictionary``), de modo que os
dados sobrevivem ao fechar/reabrir o painel de gráficos, mas são liberados
quando a conexão termina. Nada é gravado em disco.
"""

from __future__ import annotations

import time
import weakref
from collections import deque

# ~2000 amostras por interface (>2h a cada 4s); o suficiente para a janela
# do gráfico sem crescer indefinidamente.
_DEFAULT_MAXLEN = 2000


class TrafficHistory:
    """Séries temporais (timestamp, rx_bps, tx_bps) por interface."""

    def __init__(self, maxlen: int = _DEFAULT_MAXLEN):
        self.maxlen = maxlen
        self.started = time.time()
        self._series: dict[str, deque] = {}

    def add(self, iface: str, rx_bps: float, tx_bps: float,
            when: float | None = None) -> None:
        t = time.time() if when is None else when
        dq = self._series.get(iface)
        if dq is None:
            dq = deque(maxlen=self.maxlen)
            self._series[iface] = dq
        dq.append((t, float(rx_bps), float(tx_bps)))

    def samples(self, iface: str) -> list[tuple[float, float, float]]:
        return list(self._series.get(iface, ()))

    def last(self, iface: str) -> tuple[float, float, float] | None:
        dq = self._series.get(iface)
        return dq[-1] if dq else None

    def interfaces(self) -> list[str]:
        return sorted(self._series)

    def clear(self, iface: str | None = None) -> None:
        if iface is None:
            self._series.clear()
        else:
            self._series.pop(iface, None)


_STORE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def for_session(session) -> TrafficHistory:
    """Retorna (criando se preciso) o histórico associado a uma sessão."""
    history = _STORE.get(session)
    if history is None:
        history = TrafficHistory()
        _STORE[session] = history
    return history
