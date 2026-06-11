"""Execução de comandos SSH fora da thread da interface.

Todos os acessos ao equipamento passam por aqui para que a janela nunca
congele enquanto o SSH responde.

Por que Qt.QueuedConnection é obrigatório aqui:
  PySide6 não consegue determinar o "thread" de um callable Python simples
  (lambda, closure). Com AutoConnection o sinal acabaria sendo entregue
  DIRETO no worker thread — chamadas a widgets da UI nesse contexto
  são inválidas e falham silenciosamente. QueuedConnection força a
  entrega pelo event loop da thread principal, independente de quem emite.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt


class _Signals(QObject):
    finished = Signal(object)   # resultado da função
    error = Signal(str)         # mensagem de erro


class _Job(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 — erro vai para a UI
            self.signals.error.emit(str(exc))
        else:
            self.signals.finished.emit(result)


def run_async(fn, on_done, on_error, *args, **kwargs):
    """Executa ``fn`` num pool de threads e entrega o resultado na UI.

    Os callbacks on_done / on_error são chamados na thread principal via
    QueuedConnection — seguro para atualizar qualquer widget Qt.
    """
    job = _Job(fn, *args, **kwargs)
    job.signals.finished.connect(on_done, Qt.ConnectionType.QueuedConnection)
    job.signals.error.connect(on_error, Qt.ConnectionType.QueuedConnection)
    QThreadPool.globalInstance().start(job)
