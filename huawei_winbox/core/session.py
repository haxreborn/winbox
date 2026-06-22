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
# Pergunta de confirmação do VRP ("Continue? [Y/N]:") — respondida
# automaticamente; sem isso, comandos como "peer X ignore" ou "save"
# ficariam parados até estourar o timeout.
_CONFIRM_RE = re.compile(r"\[\s*Y\s*/\s*N\s*\]\s*:?\s*$|\(y/n\)\s*:?\s*$",
                         re.IGNORECASE)
# Exceção: o aviso de troca de senha que aparece logo após o login
# ("Warning: The initial password poses security risks. ... Change now?
# [Y/N]:"). Se new_password estiver definido, respondemos "Y" e fazemos a
# troca; caso contrário respondemos "N" (adiar) — mas alguns equipamentos
# não aceitam "N" e derrubam a sessão.
_DECLINE_RE = re.compile(
    r"change\s+now"
    r"|initial password"
    r"|poses security"
    r"|password.{0,40}(?:expir|chang)"
    r"|(?:expir|chang).{0,40}password"
    r"|senha.{0,40}(?:expir|alter|troc|modific)"
    r"|(?:alter|troc|modific).{0,40}senha",
    re.IGNORECASE | re.DOTALL)
# Prompts de entrada de senha durante a troca forçada no login.
_PWD_OLD_RE = re.compile(
    r"(?:old|current|original|exist[ei]ng)\s+password\s*:?\s*$"
    r"|senha\s+(?:antiga|atual|corrente)\s*:?\s*$",
    re.IGNORECASE)
_PWD_NEW_RE = re.compile(
    r"(?:new|confirm|re.?enter|repeat)\s+password\s*:?\s*$"
    r"|(?:nova|confirm[ae]|repita?)\s*(?:r?\s+a?\s*senha)?\s*:?\s*$",
    re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r|\x1b\[16D\s*\x1b\[16D")
# Extrai o nome do equipamento do prompt detectado (captura o colchete
# para distinguir visão de usuário <name> de visão de sistema [name-...]).
_HOSTNAME_RE = re.compile(r"([<\[])([^<>\[\]\r\n]+)[>\]]")

# Comandos que podem demorar bem mais que o timeout padrão.
_LONG_TIMEOUT = 90.0   # ping, traceroute, logbuffer extenso

# Mensagem quando o equipamento derruba a conexão durante o login (RST/EOF
# — no Windows aparece como "10054 - conexão cancelada pelo host remoto").
# Pode ocorrer quando o equipamento força a troca da senha no primeiro
# acesso e não aceita adiá-la com "N".
_CLOSED_MSG = (
    "A conexão foi encerrada pelo equipamento durante o login.\n\n"
    "Em alguns equipamentos isso acontece quando a troca da senha no "
    "primeiro acesso é obrigatória e não pode ser adiada. Tente conectar "
    "novamente; se persistir, conclua a troca de senha pelo console do "
    "equipamento e tente de novo.")


def confirm_answer(window: str) -> str:
    """Decide a resposta de uma confirmação ``[Y/N]`` do VRP.

    Recebe o trecho de texto que acompanha o prompt (as últimas linhas
    recebidas). Retorna ``"N"`` para o aviso de troca de senha do login e
    ``"Y"`` para qualquer outra confirmação (save, peer ignore, etc.).
    """
    return "N" if _DECLINE_RE.search(window) else "Y"


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
        # Se definido antes de connect(), o app responde "Y" ao aviso de
        # troca de senha obrigatória, envia a senha atual como senha antiga e
        # esta como nova senha. Após a troca bem-sucedida, self.password é
        # atualizado e new_password é zerado.
        self.new_password: str = ""
        # Sinaliza à UI que o equipamento EXIGE a troca de senha (recusar com
        # "N" derrubou/travou a sessão). A tela de conexão usa isto para abrir
        # o modal de nova senha e reconectar.
        self.password_change_required = False
        # Interno: True entre responder "N" ao aviso e o próximo prompt/erro.
        self._declined_change = False
        # True se o equipamento aceita "commit trial <segundos>" (detectado
        # na conexão). Apenas dispositivos two_stage podem suportar isso.
        self.supports_safe_mode = False
        # Controlado pelo botão Safe Mode da MainWindow. Quando True e o
        # equipamento suporta, configure() usa "commit trial 600" em vez de
        # "commit" — a configuração candidata fica pendente por 10 min e
        # é revertida automaticamente se não for confirmada.
        self.safe_mode = False

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
        # Lê banner/prompt inicial — hostname é extraído aqui. Se new_password
        # estiver definido, o loop interno trata a troca forçada de senha.
        self._read_until_prompt()
        # Troca concluída sem exceção: atualiza a senha ativa.
        if self.new_password:
            self.password = self.new_password
            self.new_password = ""
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

        # Detecta suporte a "commit trial": só faz sentido em dispositivos
        # two_stage. Tenta "commit trial 1" dentro da visão de sistema;
        # se o VRP não retornar "Error", o recurso existe. O trial é
        # imediatamente encerrado com "commit" antes de sair.
        if self.two_stage:
            try:
                self.run("system-view")
                probe = self.run("commit trial 60")
                if "Error" not in probe and "nknown command" not in probe:
                    self.supports_safe_mode = True
                    # Encerra o trial imediatamente (sem alterações pendentes
                    # isso é um no-op, mas garante estado limpo).
                    self.run("commit", timeout=30.0)
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
            if self.safe_mode and self.supports_safe_mode:
                commit_cmd = "commit trial 600"
            else:
                commit_cmd = "commit"
            output += f"\n> {commit_cmd}\n{self.run(commit_cmd, timeout=60.0)}"
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
        # Acumula em lista (join no final): com saídas gigantes (tabela
        # BGP completa) o "buf += chunk" e o regex sobre o buffer inteiro
        # a cada iteração ficavam O(n²) e travavam o app por minutos.
        # A detecção de prompt/More/confirm só precisa do FIM da saída.
        parts: list[str] = []
        deadline = time.time() + t
        while time.time() < deadline:
            try:
                chunk = self._shell.recv(65535)
            except socket.timeout:
                chunk = b""
            except (OSError, EOFError) as exc:
                # Reset/aborto da conexão (ex.: WSAECONNRESET 10054 no
                # Windows). Sem isto, viraria um traceback cru de socket.
                if self._declined_change:
                    self.password_change_required = True
                raise SSHError(_CLOSED_MSG) from exc
            if chunk:
                parts.append(chunk.decode("utf-8", errors="replace"))
                deadline = time.time() + t   # recebeu dados → reseta prazo
            elif self._shell.closed or self._shell.eof_received:
                # recv vazio sem timeout ⇒ canal fechado pelo equipamento.
                if self._declined_change:
                    self.password_change_required = True
                raise SSHError(_CLOSED_MSG)

            # Só o final interessa para detectar prompt/More/confirmação.
            tail_src = "".join(parts[-4:])[-4096:]
            tail = _ANSI_RE.sub("", tail_src).rstrip()
            lines = tail.splitlines() if tail else [""]
            last = lines[-1]

            if _MORE_RE.search(last):
                self._shell.send(" ")   # space avança a página
                deadline = time.time() + t
                time.sleep(0.05)
                continue

            if chunk and _CONFIRM_RE.search(last):
                # A pergunta pode ocupar mais de uma linha (o "[Y/N]" vem
                # no fim); olha as últimas linhas para decidir Y x N.
                window = "\n".join(lines[-8:])
                if _DECLINE_RE.search(window):
                    # Aviso de troca de senha: Y se já temos nova senha; senão
                    # tenta "N" (adiar). Marca _declined_change para, se a
                    # sessão cair/travar em seguida, avisar a UI que a troca
                    # é obrigatória (abre o modal de nova senha).
                    if self.new_password:
                        answer = "Y"
                    else:
                        answer = "N"
                        self._declined_change = True
                else:
                    answer = "Y"
                # [Y/N] usa modo raw no VRP — apenas \r funciona como Enter.
                self._shell.send(answer + "\r")
                deadline = time.time() + t
                time.sleep(0.1)
                continue

            # Prompts de entrada de senha durante a troca forçada no login.
            if chunk and _PWD_OLD_RE.search(last):
                self._shell.send(self.password + "\r")
                deadline = time.time() + t
                time.sleep(0.1)
                continue

            if chunk and self.new_password and _PWD_NEW_RE.search(last):
                self._shell.send(self.new_password + "\r")
                deadline = time.time() + t
                time.sleep(0.1)
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
                # Chegou ao prompt: a recusa (se houve) foi aceita.
                self._declined_change = False
                return "".join(parts)

            time.sleep(0.02)

        # Timeout: se acabamos de recusar a troca de senha, o equipamento
        # provavelmente a exige — avisa a UI para abrir o modal de nova senha.
        if self._declined_change:
            self.password_change_required = True
        tail = _ANSI_RE.sub("", "".join(parts)[-1000:]).strip()
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
