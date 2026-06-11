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
# Pergunta de confirmação do VRP ("Continue? [Y/N]:") — respondida com "y"
# automaticamente; sem isso, comandos como "peer X ignore" ou "save"
# ficariam parados até estourar o timeout.
_CONFIRM_RE = re.compile(r"\[\s*Y\s*/\s*N\s*\]\s*:?\s*$|\(y/n\)\s*:?\s*$",
                         re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r|\x1b\[16D\s*\x1b\[16D")
# Extrai o nome do equipamento do prompt detectado (captura o colchete
# para distinguir visão de usuário <name> de visão de sistema [name-...]).
_HOSTNAME_RE = re.compile(r"([<\[])([^<>\[\]\r\n]+)[>\]]")

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
        # NE/ATN (VRP em dois estágios): o prompt de configuração vem como
        # [~name] (sincronizado) ou [*name] (alterações pendentes). Quando
        # detectado, configure() envia "commit" antes de sair.
        self.two_stage = False

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
        # Detecta configuração em dois estágios (NE/ATN): entra e sai da
        # visão de sistema só para observar o prompt — [~name] marca
        # two_stage e faz configure() usar commit. Não altera nada.
        try:
            self.run("system-view")
            self.run("return")
        except SSHError:
            pass

    @property
    def connected(self) -> bool:
        return bool(self._shell and not self._shell.closed)

    def open_terminal(self, width: int = 220, height: int = 50):
        """Abre uma CONEXÃO SSH nova e exclusiva para o terminal interativo.

        Equipamentos VRP costumam limitar o número de canais por conexão
        SSH — pedir um segundo invoke_shell na mesma conexão pode ficar
        bloqueado para sempre aguardando resposta. Uma conexão dedicada
        evita o travamento e isola completamente o terminal dos painéis.

        Retorna ``(client, channel)`` — o chamador é dono dos dois e deve
        fechá-los ao terminar. Deve ser chamado fora da thread da UI.
        """
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
            transport = client.get_transport()
            transport.set_keepalive(30)
            # open_session com timeout: se o equipamento não responder ao
            # pedido de canal, falha em vez de bloquear indefinidamente.
            chan = transport.open_session(timeout=self.timeout)
            chan.get_pty(term="vt100", width=width, height=height)
            chan.invoke_shell()
        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            raise SSHError(f"Não foi possível abrir o terminal: {exc}") from exc
        chan.settimeout(0.05)
        return client, chan

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
            try:
                out = self.run(cmd)
            except SSHError as exc:
                raise SSHError(
                    f"Falha ao executar '{cmd}':\n{exc}") from exc
            outputs.append(f"> {cmd}\n{out}")
        return "\n".join(outputs)

    def configure(self, commands: list[str]) -> str:
        """Entra na visão de sistema, aplica os comandos e retorna.

        Em equipamentos de dois estágios (NE/ATN, prompt [~name]/[*name])
        um "commit" é enviado antes do return — sem ele a configuração
        candidata não é aplicada e o equipamento pergunta ao sair.
        """
        output = self.run_many(["system-view", *commands])
        if self.two_stage:
            output += f"\n> commit\n{self.run('commit', timeout=60.0)}"
        output += f"\n> return\n{self.run('return')}"
        return output

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

            if chunk and _CONFIRM_RE.search(last):
                self._shell.send("y\n")
                time.sleep(0.05)
                continue

            if _PROMPT_RE.search(tail):
                # Extrai o hostname do último prompt visível.
                m = _HOSTNAME_RE.search(last)
                if m:
                    bracket, raw_name = m.group(1), m.group(2).strip()
                    # NE/ATN: [~name] / [*name] => configuração em dois
                    # estágios (precisa de commit). O ~/* não faz parte
                    # do sysname.
                    if bracket == "[" and raw_name[:1] in "~*":
                        self.two_stage = True
                        raw_name = raw_name[1:]
                    # <name> (visão de usuário) é sempre o sysname completo.
                    # Nomes com hífen (PROM-MAR-BGP-SW01) NÃO podem ser
                    # cortados — o sufixo de sub-view só existe em [name-...]
                    # e, nesse caso, o hostname já conhecido é prefixo dele.
                    if bracket == "<":
                        self.hostname = raw_name
                    elif not raw_name.startswith(self.hostname):
                        # Visão de sistema antes de conhecermos o sysname:
                        # usa o nome inteiro até aparecer um prompt <name>.
                        self.hostname = raw_name
                return buf

            time.sleep(0.02)

        tail = _ANSI_RE.sub("", buf).strip()
        extra = (f"\n\nÚltima resposta parcial do equipamento:\n"
                 f"{tail[-400:]}" if tail else
                 " Verifique a conectividade e tente novamente.")
        raise SSHError(
            f"Tempo esgotado ({t:.0f}s) aguardando o prompt do "
            f"equipamento.{extra}")

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
