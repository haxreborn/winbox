"""Execução de comandos SSH fora da thread da interface.

Todos os acessos ao equipamento passam por aqui para que a janela nunca
congele enquanto o SSH responde.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


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
    """Executa ``fn`` num pool de threads e entrega o resultado na UI."""
    job = _Job(fn, *args, **kwargs)
    job.signals.finished.connect(on_done)
    job.signals.error.connect(on_error)
    QThreadPool.globalInstance().start(job)
