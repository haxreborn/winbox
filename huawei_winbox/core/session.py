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
_PROMPT_RE = re.compile(r"[<\[]\S[^<>\[\]\r\n]{0,79}[>\]]\s*$")
# Paginação ("---- More ----") caso screen-length não tenha sido aplicado.
_MORE_RE = re.compile(r"-+\s*[Mm]ore\s*-+")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r|\x1b\[16D\s*\x1b\[16D")
# Extrai o nome do equipamento do prompt detectado.
_HOSTNAME_RE = re.compile(r"[<\[]([^<>\[\]\r\n]+)[>\]]")

# Comandos que podem demorar bem mais que o timeout padrão.
_LONG_TIMEOUT = 90.0   # ping, traceroute, logbuffer extenso


class SSHError(Exception):
    pass


class HuaweiSession:
    """Conexão SSH persistente com um equipamento Huawei VRP."""

    def __init__(self, host: str, username: str, password: str,
                 port: int = 22, timeout: float = 15.0):
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
            raise SSHError(
                f"Não foi possível conectar a {self.host}:{self.port} — {exc}") from exc

        shell = client.invoke_shell(width=512, height=64)
        shell.settimeout(0.5)          # timeout de recv individual (não total)
        self._client = client
        self._shell = shell
        # Lê banner/prompt inicial — hostname é extraído aqui.
        self._read_until_prompt()
        # Desativa paginação nesta sessão.
        self.run("screen-length 0 temporary")

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
    def run(self, command: str, timeout: float | None = None) -> str:
        """Envia um comando e retorna a saída (sem o eco nem o prompt).

        Use ``timeout`` para comandos lentos (ping, traceroute, logbuffer).
        """
        t = timeout or self.timeout
        with self._lock:
            if not self.connected:
                raise SSHError("Sessão SSH não está conectada.")
            self._shell.send(command + "\n")
            raw = self._read_until_prompt(t)
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
    def _read_until_prompt(self, timeout: float | None = None) -> str:
        """Lê do shell até encontrar um prompt do VRP.

        Extrai o hostname do prompt encontrado (self.hostname é atualizado
        a cada resposta), lida com paginação "---- More ----" e respeita o
        timeout informado.
        """
        t = timeout or self.timeout
        buf = ""
        deadline = time.time() + t
        while time.time() < deadline:
            try:
                chunk = self._shell.recv(65535)
            except socket.timeout:
                chunk = b""
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                deadline = time.time() + t   # recebeu dados → reseta prazo

            # Trabalha sobre a versão sem ANSI para detecção de padrões.
            tail = _ANSI_RE.sub("", buf).rstrip()
            lines = tail.splitlines() if tail else [""]
            last = lines[-1]

            if _MORE_RE.search(last):
                self._shell.send(" ")
                time.sleep(0.05)
                continue

            if _PROMPT_RE.search(tail):
                # Extrai o hostname do último prompt visível.
                m = _HOSTNAME_RE.search(last)
                if m:
                    raw_name = m.group(1).strip()
                    # Remove sufixo de sub-view: [AR-GigabitEthernet0/0/1] → AR
                    self.hostname = raw_name.split("-")[0] if "-" in raw_name \
                        else raw_name
                return buf

            time.sleep(0.02)

        raise SSHError(
            f"Tempo esgotado ({t:.0f}s) aguardando resposta do equipamento. "
            "Verifique a conectividade e tente novamente.")

    @staticmethod
    def _clean(raw: str, command: str) -> str:
        text = _ANSI_RE.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        # Remove eco do comando (primeira linha que contém o comando).
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        # Remove linhas finais que são apenas prompt.
        while lines and _PROMPT_RE.search(lines[-1].strip()):
            lines = lines[:-1]
        # Remove "More" residual.
        lines = [_MORE_RE.sub("", ln) for ln in lines]
        return "\n".join(lines).strip("\n")
