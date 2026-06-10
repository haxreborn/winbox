"""Sessão SSH com equipamentos Huawei (VRP).

Mantém um shell interativo aberto via paramiko, envia comandos e
coleta a saída até detectar o prompt do VRP (<Nome> ou [Nome]).
"""

from __future__ import annotations

import re
import socket
import time
import threading

import paramiko

# Prompt do VRP: <HUAWEI>, [HUAWEI], [HUAWEI-GigabitEthernet0/0/1] etc.
_PROMPT_RE = re.compile(r"[<\[][^<>\[\]\r\n]{1,80}[>\]]\s*$")
# Paginação ("---- More ----") caso screen-length não tenha sido aplicado.
_MORE_RE = re.compile(r"-+\s*[Mm]ore\s*-+")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[16D\s*\x1b\[16D")


class SSHError(Exception):
    pass


class HuaweiSession:
    """Conexão SSH persistente com um equipamento Huawei VRP."""

    def __init__(self, host: str, username: str, password: str,
                 port: int = 22, timeout: float = 12.0):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None
        self._shell: paramiko.Channel | None = None
        self._lock = threading.Lock()
        self.hostname = host

    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException as exc:
            raise SSHError("Falha de autenticação: usuário ou senha inválidos.") from exc
        except (paramiko.SSHException, socket.error) as exc:
            raise SSHError(f"Não foi possível conectar a {self.host}:{self.port} — {exc}") from exc

        shell = client.invoke_shell(width=512, height=64)
        shell.settimeout(self.timeout)
        self._client = client
        self._shell = shell
        # Lê banner/prompt inicial e desativa paginação na sessão.
        self._read_until_prompt()
        out = self.run("screen-length 0 temporary")
        m = re.search(r"<([^<>\r\n]+)>\s*$", out) or re.search(r"<([^<>\r\n]+)>", out)
        if m:
            self.hostname = m.group(1)

    @property
    def connected(self) -> bool:
        return bool(self._shell and not self._shell.closed)

    def close(self) -> None:
        try:
            if self._shell:
                self._shell.close()
            if self._client:
                self._client.close()
        finally:
            self._shell = None
            self._client = None

    # ------------------------------------------------------------------ #
    def run(self, command: str) -> str:
        """Envia um comando e retorna a saída (sem o eco e sem o prompt)."""
        with self._lock:
            if not self.connected:
                raise SSHError("Sessão SSH não está conectada.")
            self._shell.send(command + "\n")
            raw = self._read_until_prompt()
        return self._clean(raw, command)

    def run_many(self, commands: list[str]) -> str:
        """Envia uma sequência de comandos (ex.: bloco de configuração)."""
        outputs = []
        for cmd in commands:
            outputs.append(f"> {cmd}\n{self.run(cmd)}")
        return "\n".join(outputs)

    def configure(self, commands: list[str]) -> str:
        """Entra na visão de sistema, aplica os comandos e retorna."""
        seq = ["system-view", *commands, "return"]
        return self.run_many(seq)

    # ------------------------------------------------------------------ #
    def _read_until_prompt(self) -> str:
        buf = ""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535).decode("utf-8", errors="replace")
                buf += chunk
                deadline = time.time() + self.timeout
                tail = _ANSI_RE.sub("", buf).rstrip()
                if _MORE_RE.search(tail.splitlines()[-1] if tail else ""):
                    self._shell.send(" ")
                    continue
                if _PROMPT_RE.search(tail):
                    return buf
            else:
                time.sleep(0.05)
        raise SSHError("Tempo esgotado aguardando resposta do equipamento.")

    @staticmethod
    def _clean(raw: str, command: str) -> str:
        text = _ANSI_RE.sub("", raw).replace("\r", "")
        lines = text.split("\n")
        # Remove eco do comando e linha final de prompt.
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        while lines and _PROMPT_RE.search(lines[-1].strip()):
            lines = lines[:-1]
        # Remove "More" residual.
        lines = [_MORE_RE.sub("", ln) for ln in lines]
        return "\n".join(lines).strip("\n")
