"""Execução de comandos SSH fora da thread da interface.

Todos os acessos ao equipamento passam por aqui para que a janela nunca
congele enquanto o SSH responde.

Por que Qt.QueuedConnection é obrigatório aqui:
  PySide6 não consegue determinar o "thread" de um callable Python simples
  (lambda, closure). Com AutoConnection o sinal acabaria sendo entregue
  DIRETO no worker thread — chamadas a widgets da UI nesse contexto
  são inválidas e falham silenciosamente. QueuedConnection força a
  entrega pelo event loop da thread principal, independente de quem emite.

Por que o job fica retido em _ACTIVE até o callback rodar:
  QThreadPool deleta o QRunnable assim que run() retorna (autoDelete).
  Com QueuedConnection o emit apenas POSTA um evento — se o objeto
  _Signals for destruído junto com o job antes do event loop da thread
  principal processar esse evento, o callback nunca é chamado e a UI
  fica "consultando" para sempre.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt


class _Signals(QObject):
    finished = Signal(object)   # resultado da função
    error = Signal(str)         # mensagem de erro


class _Job(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        # O pool não pode deletar o runnable: o sinal enfileirado ainda
        # referencia self.signals até a thread principal processá-lo.
        self.setAutoDelete(False)
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


# Jobs em voo — mantém o QRunnable e o _Signals vivos até a entrega.
_ACTIVE: set[_Job] = set()


def run_async(fn, on_done, on_error, *args, **kwargs):
    """Executa ``fn`` num pool de threads e entrega o resultado na UI.

    Os callbacks on_done / on_error são chamados na thread principal via
    QueuedConnection — seguro para atualizar qualquer widget Qt.
    """
    job = _Job(fn, *args, **kwargs)

    def _done(result, _job=job):
        _ACTIVE.discard(_job)
        on_done(result)

    def _error(message, _job=job):
        _ACTIVE.discard(_job)
        on_error(message)

    job.signals.finished.connect(_done, Qt.ConnectionType.QueuedConnection)
    job.signals.error.connect(_error, Qt.ConnectionType.QueuedConnection)
    _ACTIVE.add(job)
    QThreadPool.globalInstance().start(job)
