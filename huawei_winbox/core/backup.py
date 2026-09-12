"""Histórico de configurações em disco (``~/.huaweibox/backups/<host>/``).

O painel de Configuração já mostrava a config em execução e a comparava
com a salva na flash, mas tudo isso morria ao fechar o aplicativo. Aqui as
cópias ficam guardadas: dá para voltar semanas e ver **o que mudou entre
duas datas** — que é a pergunta real quando algo parou de funcionar e
ninguém sabe o que mexeram.

Cada arquivo é a configuração inteira em texto, nomeado pelo instante da
coleta::

    ~/.huaweibox/backups/10.20.30.40/20250912-140211.cfg
    ~/.huaweibox/backups/10.20.30.40/20250912-181530-antes-do-bgp.cfg

Cópias idênticas à anterior **não** são gravadas: conectar dez vezes num
equipamento parado não deve gerar dez arquivos iguais. O histórico é
podado automaticamente para não crescer sem fim.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

_CONFIG_DIR = Path.home() / ".huaweibox"
_BACKUP_DIR = _CONFIG_DIR / "backups"

# Quantas cópias manter por equipamento.
DEFAULT_KEEP = 40
_LABEL_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DOT_RUN = re.compile(r"\.{2,}")


def _ascii_fold(text: str) -> str:
    """'produção' -> 'producao'.

    Só para compor o NOME DO ARQUIVO, que precisa ser seguro em qualquer
    sistema de arquivos. Sem isto, "automático" virava "autom_tico" e
    quem abrisse a pasta veria um buraco no meio da palavra. O rótulo
    original continua íntegro no índice.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn")
_STAMP_RE = re.compile(r"^(\d{8})-(\d{6})(?:-(.*))?$")


def _host_dir(host: str) -> Path:
    """Diretório do equipamento (o endereço vira nome de pasta seguro).

    O ponto é mantido — os endereços IP dependem dele — mas sequências de
    dois ou mais viram ``_``: um "host" como ``../../etc`` não pode virar
    caminho para fora da pasta de backups.
    """
    safe = _LABEL_SAFE.sub("_", _ascii_fold(host.strip()))
    safe = _DOT_RUN.sub("_", safe).strip("._") or "desconhecido"
    directory = _BACKUP_DIR / safe
    # Rede de segurança: se ainda assim o caminho escapar, não usa.
    if _BACKUP_DIR not in directory.parents:
        return _BACKUP_DIR / "desconhecido"
    return directory


def digest(text: str) -> str:
    """Impressão digital do conteúdo, usada para evitar cópias repetidas."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# ── rótulos ──────────────────────────────────────────────────────────── #
# O nome do arquivo tem que ser seguro em qualquer sistema de arquivos, o
# que descarta acentos e espaços. Mas o rótulo é um bilhete que a pessoa
# escreveu ("antes do BGP", "produção") e precisa voltar exatamente como
# foi digitado — derivá-lo do nome do arquivo devolvia "produ ao". Por
# isso os rótulos moram num índice ao lado das cópias; se ele sumir, o
# nome do arquivo ainda serve de aproximação.
def _labels_path(host: str) -> Path:
    return _host_dir(host) / "labels.json"


def _read_labels(host: str) -> dict:
    try:
        data = json.loads(_labels_path(host).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_labels(host: str, labels: dict) -> None:
    try:
        _labels_path(host).write_text(
            json.dumps(labels, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def list_backups(host: str) -> list[dict]:
    """Cópias existentes, da mais recente para a mais antiga."""
    directory = _host_dir(host)
    if not directory.is_dir():
        return []
    labels = _read_labels(host)
    entries = []
    for path in directory.glob("*.cfg"):
        match = _STAMP_RE.match(path.stem)
        if not match:
            continue
        date, clock, from_name = match.groups()
        try:
            when = time.mktime(time.strptime(date + clock, "%Y%m%d%H%M%S"))
        except ValueError:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append({
            "path": path,
            "when": when,
            "label": labels.get(path.stem, (from_name or "").replace("_", " ")),
            "size": size,
            "name": path.stem,
        })
    entries.sort(key=lambda item: item["when"], reverse=True)
    return entries


def read(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def latest(host: str) -> dict | None:
    entries = list_backups(host)
    return entries[0] if entries else None


def save(host: str, text: str, label: str = "",
         keep: int = DEFAULT_KEEP) -> dict | None:
    """Grava uma cópia. Retorna ``None`` se for igual à cópia anterior.

    Devolver ``None`` em vez de gravar é o comportamento certo para o
    backup automático da conexão: sem alteração, sem arquivo novo.
    """
    if not text.strip():
        return None
    previous = latest(host)
    if previous is not None and digest(read(previous["path"])) == digest(text):
        return None

    directory = _host_dir(host)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = _LABEL_SAFE.sub("_", _ascii_fold(label.strip())).strip("_")[:40]
    name = f"{stamp}-{suffix}.cfg" if suffix else f"{stamp}.cfg"
    path = directory / name
    # Um segundo pode conter dois pedidos; não sobrescreve o primeiro.
    counter = 1
    while path.exists():
        path = directory / f"{stamp}-{counter}{('-' + suffix) if suffix else ''}.cfg"
        counter += 1
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    if label.strip():
        labels = _read_labels(host)
        labels[path.stem] = label.strip()
        _write_labels(host, labels)
    prune(host, keep)
    return {
        "path": path,
        "when": time.time(),
        "label": label,
        "size": len(text.encode("utf-8", "replace")),
        "name": path.stem,
    }


def prune(host: str, keep: int = DEFAULT_KEEP) -> int:
    """Apaga as cópias mais antigas além de ``keep``. Retorna quantas saíram."""
    entries = list_backups(host)
    removed = 0
    dropped = []
    for entry in entries[max(1, keep):]:
        try:
            entry["path"].unlink()
            dropped.append(entry["name"])
            removed += 1
        except OSError:
            pass
    if dropped:
        # Tira do índice os rótulos das cópias apagadas, senão ele cresce
        # para sempre com entradas órfãs.
        labels = _read_labels(host)
        if any(labels.pop(name, None) is not None for name in dropped):
            _write_labels(host, labels)
    return removed


def directory_for(host: str) -> Path:
    return _host_dir(host)
