"""Preferências de trabalho por equipamento (``~/.huaweibox/workspace.json``).

Guarda o que o operador não deveria ter que refazer a cada conexão:

* quais janelas estavam abertas, onde e de que tamanho;
* quais interfaces estavam marcadas no gráfico, e em que modo/janela/escala;
* a fonte da telemetria, o intervalo de amostragem e a comunidade SNMP.

Tudo é indexado por ``host`` (o mesmo equipamento visto de qualquer
usuário mantém o layout). O arquivo é texto simples e **nenhuma senha
passa por aqui** — credenciais continuam só em ``devices.json``.

Se o arquivo estiver corrompido ou o disco não puder ser escrito, o
aplicativo segue normalmente com os padrões: preferência de layout nunca
deve impedir alguém de conectar no equipamento.
"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".huaweibox"
_FILE = _CONFIG_DIR / "workspace.json"

# Teto de equipamentos lembrados: o arquivo não deve crescer para sempre.
_MAX_HOSTS = 60


def _load_all() -> dict:
    if not _FILE.exists():
        return {}
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Escreve num temporário e troca: uma queda no meio da gravação não
        # deixa o arquivo pela metade.
        temporary = _FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(_FILE)
    except OSError:
        pass          # preferência de layout não impede o uso do aplicativo


def load(host: str) -> dict:
    """Preferências salvas de um equipamento (``{}`` se não houver)."""
    entry = _load_all().get(host)
    return entry if isinstance(entry, dict) else {}


def save(host: str, values: dict) -> None:
    """Mescla ``values`` nas preferências do equipamento."""
    if not host:
        return
    data = _load_all()
    entry = data.get(host)
    if not isinstance(entry, dict):
        entry = {}
    entry.update(values)
    data[host] = entry
    if len(data) > _MAX_HOSTS:
        # Descarta os mais antigos pela ordem de inserção do dicionário.
        for key in list(data)[:len(data) - _MAX_HOSTS]:
            if key != host:
                data.pop(key, None)
    _save_all(data)


def panel_state(host: str, key: str) -> dict:
    """Estado salvo de um painel específico deste equipamento."""
    panels = load(host).get("panels")
    if isinstance(panels, dict):
        entry = panels.get(key)
        if isinstance(entry, dict):
            return entry
    return {}


def forget(host: str) -> None:
    data = _load_all()
    if data.pop(host, None) is not None:
        _save_all(data)
