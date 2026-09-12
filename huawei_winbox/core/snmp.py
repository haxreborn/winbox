"""Cliente SNMP v2c mínimo e autossuficiente (sem dependências externas).

Implementa o suficiente para *ler* dados de um equipamento que já tenha o
agente SNMP habilitado: GET, GET-NEXT e walk de uma sub-árvore. A
codificação/decodificação BER é feita à mão — assim o aplicativo continua
dependendo apenas de PySide6 + paramiko e funciona em Windows/Linux/macOS
sem instalar nada a mais.

Uso típico (em uma thread de trabalho, nunca na thread da UI)::

    cli = SnmpV2cClient("10.0.0.1", community="public")
    print(cli.get([OID_SYS_NAME, OID_SYS_UPTIME]))
    nomes = cli.iface_index_map()   # {"GigabitEthernet0/0/1": 3, ...}

Só leitura — não há SET. SNMP v2c trafega a comunidade em texto claro, então
o gráfico de tráfego usa isto apenas para consulta; a configuração do agente
continua sendo feita pelo painel SNMP via SSH.
"""

from __future__ import annotations

import socket
import threading
import time

# ── OIDs usados pelo aplicativo ─────────────────────────────────────────── #
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# Tabela de interfaces (IF-MIB)
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"        # ifDescr.<idx>
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"        # ifSpeed.<idx> (bits/s, 32 bits)
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"  # ifOperStatus.<idx>
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"   # ifInOctets.<idx> (Counter32)
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"  # ifOutOctets.<idx> (Counter32)
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"      # ifName.<idx>
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"   # ifHCInOctets (Counter64)
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"  # ifHCOutOctets (Counter64)
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"     # ifHighSpeed (Mbit/s)
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"          # ifAlias (descrição)
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"       # ifAdminStatus
OID_IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"       # ifInDiscards
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"         # ifInErrors
OID_IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"      # ifOutDiscards
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"        # ifOutErrors

# Entity-MIB / extensão Huawei — CPU, memória e temperatura por placa.
# Presente nas séries S, AR, NE e ATN; é a via mais barata (e mais
# frequente) de ler a carga do equipamento sem gastar a sessão SSH.
OID_ENT_PHYSICAL_NAME = "1.3.6.1.2.1.47.1.1.1.1.7"        # entPhysicalName
OID_HW_ENTITY_CPU = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5"   # hwEntityCpuUsage
OID_HW_ENTITY_MEM_SIZE = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.6"
OID_HW_ENTITY_MEM = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7"   # hwEntityMemUsage
OID_HW_ENTITY_TEMP = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.11"  # °C

# Estado operacional (ifOperStatus / ifAdminStatus) em texto.
IF_STATUS = {1: "up", 2: "down", 3: "testing", 4: "unknown",
             5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}

# Tags BER
_INTEGER = 0x02
_OCTET_STRING = 0x04
_NULL = 0x05
_OID = 0x06
_SEQUENCE = 0x30
_IPADDRESS = 0x40
_COUNTER32 = 0x41
_GAUGE32 = 0x42
_TIMETICKS = 0x43
_OPAQUE = 0x44
_COUNTER64 = 0x46
_GET_REQUEST = 0xA0
_GETNEXT_REQUEST = 0xA1
_GET_RESPONSE = 0xA2
_GETBULK_REQUEST = 0xA5
# Exceções de varbind (SNMPv2) na posição do valor
_NO_SUCH_OBJECT = 0x80
_NO_SUCH_INSTANCE = 0x81
_END_OF_MIB_VIEW = 0x82

_SNMP_VERSION_2C = 1   # 0 = v1, 1 = v2c


_ERROR_STATUS = {
    1: "tooBig", 2: "noSuchName", 3: "badValue", 4: "readOnly",
    5: "genErr", 6: "noAccess", 7: "wrongType", 8: "wrongLength",
    9: "wrongEncoding", 10: "wrongValue", 11: "noCreation",
    12: "inconsistentValue", 13: "resourceUnavailable",
    14: "commitFailed", 15: "undoFailed", 16: "authorizationError",
    17: "notWritable", 18: "inconsistentName",
}


class SnmpError(Exception):
    pass


class SnmpTimeout(SnmpError):
    """O agente não respondeu dentro do prazo."""


class SnmpTooBig(SnmpError):
    """A resposta não coube no PDU — reduzir max-repetitions e repetir."""


class _WrongRequestId(Exception):
    """Interno: resposta de um request-id anterior (atrasada)."""


class _EndOfMib:
    """Sentinela retornado quando o agente sinaliza fim da sub-árvore."""
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - cosmético
        return "<endOfMibView>"


END_OF_MIB = _EndOfMib()


# ── Codificação BER ─────────────────────────────────────────────────────── #
def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    out = b""
    while length:
        out = bytes([length & 0xFF]) + out
        length >>= 8
    return bytes([0x80 | len(out)]) + out


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_integer(value: int) -> bytes:
    # Inteiro com sinal, mínimo de bytes (complemento de dois).
    if value == 0:
        return _tlv(_INTEGER, b"\x00")
    out = b""
    v = value
    if v > 0:
        while v:
            out = bytes([v & 0xFF]) + out
            v >>= 8
        if out[0] & 0x80:          # bit de sinal acidental → prefixa 0x00
            out = b"\x00" + out
    else:
        # Complemento de dois para negativos (raro aqui, mas correto).
        nbytes = (value.bit_length() + 8) // 8
        out = (value & ((1 << (8 * nbytes)) - 1)).to_bytes(nbytes, "big")
    return _tlv(_INTEGER, out)


def _encode_oid(oid: str) -> bytes:
    parts = [int(p) for p in oid.strip(".").split(".")]
    if len(parts) < 2:
        raise SnmpError(f"OID inválido: {oid}")
    body = bytearray([40 * parts[0] + parts[1]])
    for sub in parts[2:]:
        if sub < 0x80:
            body.append(sub)
            continue
        chunk = bytearray([sub & 0x7F])
        sub >>= 7
        while sub:
            chunk.insert(0, (sub & 0x7F) | 0x80)
            sub >>= 7
        body.extend(chunk)
    return _tlv(_OID, bytes(body))


# ── Decodificação BER ───────────────────────────────────────────────────── #
def _read_tlv(data: bytes, idx: int) -> tuple[int, bytes, int]:
    """Lê um TLV a partir de ``idx``. Retorna (tag, valor, próximo_idx)."""
    if idx + 1 > len(data):
        raise SnmpError("Pacote SNMP truncado (tag).")
    tag = data[idx]
    idx += 1
    first = data[idx]
    idx += 1
    if first < 0x80:
        length = first
    else:
        nbytes = first & 0x7F
        if idx + nbytes > len(data):
            raise SnmpError("Pacote SNMP truncado (comprimento).")
        length = int.from_bytes(data[idx:idx + nbytes], "big")
        idx += nbytes
    if idx + length > len(data):
        raise SnmpError("Pacote SNMP truncado (valor).")
    return tag, data[idx:idx + length], idx + length


def _decode_oid(value: bytes) -> str:
    if not value:
        return ""
    first = value[0]
    subids = [first // 40, first % 40]
    acc = 0
    for byte in value[1:]:
        acc = (acc << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            subids.append(acc)
            acc = 0
    return ".".join(str(s) for s in subids)


def _oid_key(oid: str) -> tuple:
    """Chave de ordenação numérica de um OID ('1.3.6.1.2' -> (1,3,6,1,2)).

    Comparar OIDs como texto está errado ('.10' < '.9' lexicograficamente);
    os walks dependem da ordem numérica correta para saber se o agente
    avançou.
    """
    try:
        return tuple(int(p) for p in oid.strip(".").split(".") if p)
    except ValueError:
        return (0,)


def _decode_value(tag: int, value: bytes):
    if tag == _INTEGER:
        return int.from_bytes(value, "big", signed=True) if value else 0
    if tag in (_COUNTER32, _GAUGE32, _TIMETICKS, _COUNTER64):
        return int.from_bytes(value, "big", signed=False) if value else 0
    if tag == _OCTET_STRING:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1")
    if tag == _OID:
        return _decode_oid(value)
    if tag == _IPADDRESS:
        return ".".join(str(b) for b in value)
    if tag == _NULL:
        return None
    if tag in (_NO_SUCH_OBJECT, _NO_SUCH_INSTANCE, _END_OF_MIB_VIEW):
        return END_OF_MIB
    return value


# ── Cliente ─────────────────────────────────────────────────────────────── #
class SnmpV2cClient:
    """Cliente SNMP v2c somente-leitura sobre UDP."""

    def __init__(self, host: str, community: str = "public",
                 port: int = 161, timeout: float = 2.0, retries: int = 1):
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = max(0, retries)
        self._request_id = int(time.time()) & 0x7FFFFFFF
        # Socket reutilizado entre transações: a amostragem em tempo real
        # dispara dezenas de PDUs por minuto e criar/destruir um socket por
        # PDU custa syscalls à toa. Protegido por lock — o motor de
        # telemetria e a UI podem consultar o mesmo cliente.
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        # Definido como False quando o agente rejeita GET-BULK (v1 puro ou
        # agente limitado); a partir daí os walks caem para GET-NEXT.
        self.supports_bulk = True

    # -- baixo nível ------------------------------------------------------- #
    def _next_id(self) -> int:
        self._request_id = (self._request_id + 1) & 0x7FFFFFFF
        return self._request_id or 1

    def _socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(self.timeout)
        return self._sock

    def _drop_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self) -> None:
        """Libera o socket UDP (opcional — o GC também o faria)."""
        with self._lock:
            self._drop_socket()

    def _build_message(self, pdu_tag: int, oids: list[str], request_id: int,
                       field_a: int = 0, field_b: int = 0) -> bytes:
        """Monta a mensagem SNMP.

        ``field_a``/``field_b`` são error-status/error-index nas PDUs GET e
        GET-NEXT e, na GET-BULK, non-repeaters/max-repetitions (a RFC 3416
        reaproveita as mesmas posições).
        """
        varbinds = b""
        for oid in oids:
            varbinds += _tlv(_SEQUENCE,
                             _encode_oid(oid) + _tlv(_NULL, b""))
        varbind_list = _tlv(_SEQUENCE, varbinds)
        pdu = _tlv(pdu_tag,
                   _encode_integer(request_id)
                   + _encode_integer(field_a)
                   + _encode_integer(field_b)
                   + varbind_list)
        message = _tlv(_SEQUENCE,
                       _encode_integer(_SNMP_VERSION_2C)
                       + _tlv(_OCTET_STRING, self.community.encode())
                       + pdu)
        return message

    def _transact(self, pdu_tag: int, oids: list[str],
                  field_a: int = 0, field_b: int = 0) -> list[tuple]:
        request_id = self._next_id()
        message = self._build_message(pdu_tag, oids, request_id,
                                      field_a, field_b)
        with self._lock:
            last_exc: Exception | None = None
            for _attempt in range(self.retries + 1):
                try:
                    sock = self._socket()
                    sock.sendto(message, (self.host, self.port))
                    deadline = time.monotonic() + self.timeout
                    # Respostas atrasadas de uma tentativa anterior ainda
                    # podem estar na fila do socket; só aceita a que casa
                    # com o request-id atual.
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise socket.timeout()
                        sock.settimeout(remaining)
                        data, _addr = sock.recvfrom(65535)
                        try:
                            return self._parse_response(data, request_id)
                        except _WrongRequestId:
                            continue
                except socket.timeout as exc:
                    last_exc = exc
                    continue
                except OSError as exc:
                    self._drop_socket()
                    raise SnmpError(f"Erro de rede SNMP: {exc}") from exc
            raise SnmpTimeout(
                f"Sem resposta SNMP de {self.host}:{self.port} "
                f"(timeout {self.timeout:.0f}s). Verifique a comunidade e se "
                f"o agente está habilitado.") from last_exc

    @staticmethod
    def _parse_response(data: bytes, request_id: int) -> list[tuple]:
        tag, body, _ = _read_tlv(data, 0)
        if tag != _SEQUENCE:
            raise SnmpError("Resposta SNMP malformada (sem SEQUENCE).")
        idx = 0
        _, _version, idx = _read_tlv(body, idx)          # version
        _, _community, idx = _read_tlv(body, idx)        # community
        pdu_tag, pdu, _ = _read_tlv(body, idx)
        if pdu_tag != _GET_RESPONSE:
            raise SnmpError(f"PDU inesperada na resposta: 0x{pdu_tag:02X}")
        pidx = 0
        _, rid_bytes, pidx = _read_tlv(pdu, pidx)
        _, err_bytes, pidx = _read_tlv(pdu, pidx)
        _, _erridx_bytes, pidx = _read_tlv(pdu, pidx)
        if int.from_bytes(rid_bytes, "big", signed=True) != request_id:
            # Resposta atrasada de uma tentativa anterior — descarta e
            # continua esperando a correta.
            raise _WrongRequestId()
        error_status = int.from_bytes(err_bytes, "big", signed=True)
        if error_status == 1:
            raise SnmpTooBig("Resposta SNMP maior que o PDU suportado.")
        if error_status:
            raise SnmpError(
                f"Agente retornou erro SNMP (error-status={error_status}: "
                f"{_ERROR_STATUS.get(error_status, 'desconhecido')}).")
        _, vb_list, _ = _read_tlv(pdu, pidx)
        result: list[tuple] = []
        vidx = 0
        while vidx < len(vb_list):
            _, vb, vidx = _read_tlv(vb_list, vidx)
            inner = 0
            otag, oval, inner = _read_tlv(vb, inner)
            vtag, vval, inner = _read_tlv(vb, inner)
            result.append((_decode_oid(oval), vtag, _decode_value(vtag, vval)))
        return result

    # -- API ---------------------------------------------------------------- #
    def get(self, oids: list[str]) -> dict[str, object]:
        """GET de uma lista de OIDs -> {oid_str: valor decodificado}."""
        if not oids:
            return {}
        varbinds = self._transact(_GET_REQUEST, oids)
        return {oid: value for oid, _tag, value in varbinds}

    def get_one(self, oid: str):
        """GET de um único OID -> valor (ou None)."""
        return self.get([oid]).get(oid)

    def get_next(self, oid: str) -> tuple[str, object] | None:
        """GET-NEXT -> (próximo_oid, valor) ou None no fim da MIB."""
        varbinds = self._transact(_GETNEXT_REQUEST, [oid])
        if not varbinds:
            return None
        next_oid, _tag, value = varbinds[0]
        if value is END_OF_MIB:
            return None
        return next_oid, value

    def walk(self, base_oid: str, max_rows: int = 10000) -> list[tuple]:
        """Percorre uma sub-árvore via GET-NEXT -> [(oid, valor), ...]."""
        base = base_oid.strip(".")
        prefix = base + "."
        results: list[tuple] = []
        current = base
        for _ in range(max_rows):
            step = self.get_next(current)
            if step is None:
                break
            next_oid, value = step
            if not (next_oid == base or next_oid.startswith(prefix)):
                break
            if next_oid == current:        # proteção contra laço infinito
                break
            results.append((next_oid, value))
            current = next_oid
        return results

    # -- GET-BULK (RFC 3416) ------------------------------------------------ #
    def get_bulk(self, oids: list[str], non_repeaters: int = 0,
                 max_repetitions: int = 25) -> list[tuple]:
        """GET-BULK cru -> [(oid, tag, valor), ...] na ordem da resposta."""
        return self._transact(_GETBULK_REQUEST, oids,
                              non_repeaters, max_repetitions)

    def walk_columns(self, bases: list[str], max_repetitions: int = 25,
                     max_rows: int = 20000) -> dict[str, dict[str, object]]:
        """Percorre VÁRIAS colunas de uma tabela numa só passada.

        Esta é a peça que torna o gráfico multi-interface viável: em vez de
        um GET por interface por amostra (48 portas ⇒ 48 idas e voltas), um
        GET-BULK devolve ~25 linhas de TODAS as colunas pedidas por pacote —
        uma placa de 48 portas cabe em 2 ou 3 PDUs.

        Retorna ``{base_oid: {sufixo: valor}}``, onde o sufixo é o índice da
        linha (o ifIndex, no caso da IF-MIB). Cai automaticamente para
        GET-NEXT em agentes que não implementam GET-BULK.
        """
        bases = [b.strip(".") for b in bases]
        if not bases:
            return {}
        if not self.supports_bulk:
            return {b: {oid[len(b) + 1:]: val for oid, val in self.walk(b)}
                    for b in bases}

        results: dict[str, dict[str, object]] = {b: {} for b in bases}
        cursors = {b: b for b in bases}
        active = list(bases)
        reps = max(1, max_repetitions)
        rows = 0
        while active and rows < max_rows:
            try:
                varbinds = self.get_bulk([cursors[b] for b in active],
                                         0, reps)
            except SnmpTooBig:
                if reps > 1:
                    reps = max(1, reps // 2)
                    continue
                break
            except SnmpTimeout:
                raise
            except SnmpError:
                # Agente não digere GET-BULK: desiste do modo bulk de vez.
                self.supports_bulk = False
                return {b: {oid[len(b) + 1:]: val for oid, val in self.walk(b)}
                        for b in bases}
            if not varbinds:
                break

            width = len(active)
            done: set[str] = set()
            progressed = False
            for position, (oid, _tag, value) in enumerate(varbinds):
                base = active[position % width]
                if base in done:
                    continue
                in_subtree = oid == base or oid.startswith(base + ".")
                if value is END_OF_MIB or not in_subtree:
                    done.add(base)
                    continue
                if _oid_key(oid) <= _oid_key(cursors[base]):
                    # Agente não avançou (ou repetiu): encerra esta coluna
                    # para não entrar em laço infinito.
                    done.add(base)
                    continue
                results[base][oid[len(base) + 1:]] = value
                cursors[base] = oid
                progressed = True
                rows += 1
            active = [b for b in active if b not in done]
            if not progressed:
                break
        return results

    # -- conveniências de alto nível ---------------------------------------- #
    def iface_index_map(self) -> dict[str, int]:
        """Mapa {nome_da_interface: ifIndex} a partir de ifName (ou ifDescr).

        Usa o sufixo do OID como ifIndex. Cai para ifDescr quando o agente
        não expõe ifName (IF-MIB antigo).
        """
        mapping: dict[str, int] = {}
        for base in (OID_IF_NAME, OID_IF_DESCR):
            for oid, value in self.walk(base):
                try:
                    index = int(oid.rsplit(".", 1)[1])
                except (ValueError, IndexError):
                    continue
                name = str(value).strip()
                if name and name not in mapping:
                    mapping[name] = index
            if mapping:
                break
        return mapping

    def octet_counters(self, ifindex: int) -> tuple[int, int]:
        """Octetos de entrada/saída de uma interface (in, out).

        Prefere os contadores de 64 bits (ifHCInOctets/ifHCOutOctets) e cai
        para os de 32 bits quando indisponíveis.
        """
        hc = self.get([f"{OID_IF_HC_IN_OCTETS}.{ifindex}",
                       f"{OID_IF_HC_OUT_OCTETS}.{ifindex}"])
        in_oct = hc.get(f"{OID_IF_HC_IN_OCTETS}.{ifindex}")
        out_oct = hc.get(f"{OID_IF_HC_OUT_OCTETS}.{ifindex}")
        if isinstance(in_oct, int) and isinstance(out_oct, int):
            return in_oct, out_oct
        low = self.get([f"{OID_IF_IN_OCTETS}.{ifindex}",
                        f"{OID_IF_OUT_OCTETS}.{ifindex}"])
        in_oct = low.get(f"{OID_IF_IN_OCTETS}.{ifindex}")
        out_oct = low.get(f"{OID_IF_OUT_OCTETS}.{ifindex}")
        if isinstance(in_oct, int) and isinstance(out_oct, int):
            return in_oct, out_oct
        raise SnmpError(
            f"Interface ifIndex={ifindex} não retornou contadores de octetos.")

    # -- amostragem de TODAS as interfaces numa só rajada -------------------- #
    def interface_metadata(self) -> dict[int, dict]:
        """Nome, alias, velocidade e estado de todas as interfaces.

        Muda raramente — o motor de telemetria lê isto uma vez e depois só
        busca os contadores, que é o que precisa ser rápido.
        """
        cols = self.walk_columns([OID_IF_NAME, OID_IF_ALIAS,
                                  OID_IF_HIGH_SPEED, OID_IF_OPER_STATUS,
                                  OID_IF_ADMIN_STATUS])
        names = cols.get(OID_IF_NAME) or {}
        if not names:
            # IF-MIB antiga: sem ifXTable, usa ifDescr/ifSpeed.
            legacy = self.walk_columns([OID_IF_DESCR, OID_IF_SPEED,
                                        OID_IF_OPER_STATUS,
                                        OID_IF_ADMIN_STATUS])
            names = legacy.get(OID_IF_DESCR) or {}
            speeds = {k: _as_int(v) for k, v in
                      (legacy.get(OID_IF_SPEED) or {}).items()}
            aliases: dict[str, object] = {}
            oper = legacy.get(OID_IF_OPER_STATUS) or {}
            admin = legacy.get(OID_IF_ADMIN_STATUS) or {}
        else:
            aliases = cols.get(OID_IF_ALIAS) or {}
            # ifHighSpeed vem em Mbit/s.
            speeds = {k: _as_int(v) * 1_000_000
                      for k, v in (cols.get(OID_IF_HIGH_SPEED) or {}).items()}
            oper = cols.get(OID_IF_OPER_STATUS) or {}
            admin = cols.get(OID_IF_ADMIN_STATUS) or {}

        meta: dict[int, dict] = {}
        for suffix, name in names.items():
            try:
                index = int(suffix)
            except ValueError:
                continue
            meta[index] = {
                "name": str(name).strip(),
                "alias": str(aliases.get(suffix, "") or "").strip(),
                "speed_bps": speeds.get(suffix, 0),
                "oper": IF_STATUS.get(_as_int(oper.get(suffix)), "unknown"),
                "admin": IF_STATUS.get(_as_int(admin.get(suffix)), "unknown"),
            }
        return meta

    def interface_counters(self) -> tuple[dict[int, tuple[int, int]], int]:
        """Contadores de octetos de TODAS as interfaces numa só passada.

        Retorna ``({ifIndex: (in_octets, out_octets)}, largura_em_bits)``.
        A largura (64 ou 32) diz ao chamador em que ponto o contador dá a
        volta — essencial para não desenhar um pico falso de terabits
        quando um Counter32 zera.
        """
        cols = self.walk_columns([OID_IF_HC_IN_OCTETS, OID_IF_HC_OUT_OCTETS])
        ins = cols.get(OID_IF_HC_IN_OCTETS) or {}
        outs = cols.get(OID_IF_HC_OUT_OCTETS) or {}
        width = 64
        if not ins or not outs:
            cols = self.walk_columns([OID_IF_IN_OCTETS, OID_IF_OUT_OCTETS])
            ins = cols.get(OID_IF_IN_OCTETS) or {}
            outs = cols.get(OID_IF_OUT_OCTETS) or {}
            width = 32
        counters: dict[int, tuple[int, int]] = {}
        for suffix, in_val in ins.items():
            out_val = outs.get(suffix)
            if out_val is None:
                continue
            try:
                index = int(suffix)
            except ValueError:
                continue
            counters[index] = (_as_int(in_val), _as_int(out_val))
        if not counters:
            raise SnmpError("O agente não expôs contadores de octetos "
                            "(ifHCInOctets nem ifInOctets).")
        return counters, width

    def interface_errors(self) -> dict[int, dict[str, int]]:
        """Erros e descartes por interface (para o painel de saúde)."""
        cols = self.walk_columns([OID_IF_IN_ERRORS, OID_IF_OUT_ERRORS,
                                  OID_IF_IN_DISCARDS, OID_IF_OUT_DISCARDS])
        out: dict[int, dict[str, int]] = {}
        mapping = [("in_errors", OID_IF_IN_ERRORS),
                   ("out_errors", OID_IF_OUT_ERRORS),
                   ("in_discards", OID_IF_IN_DISCARDS),
                   ("out_discards", OID_IF_OUT_DISCARDS)]
        for suffix in (cols.get(OID_IF_IN_ERRORS) or {}):
            try:
                index = int(suffix)
            except ValueError:
                continue
            out[index] = {key: _as_int((cols.get(oid) or {}).get(suffix))
                          for key, oid in mapping}
        return out

    def system_health(self) -> dict:
        """CPU, memória e temperatura via extensão Huawei da ENTITY-MIB.

        Retorna ``{"cpu": %, "memory": %, "temperature": °C, "entities":
        [...]}``. Valores ausentes vêm como ``None`` — em vários modelos só
        as entidades "board" preenchem os três campos, e as demais ficam em
        zero (essas são descartadas).
        """
        cols = self.walk_columns([OID_HW_ENTITY_CPU, OID_HW_ENTITY_MEM,
                                  OID_HW_ENTITY_TEMP])
        cpus = cols.get(OID_HW_ENTITY_CPU) or {}
        mems = cols.get(OID_HW_ENTITY_MEM) or {}
        temps = cols.get(OID_HW_ENTITY_TEMP) or {}
        entities = []
        for suffix in sorted(set(cpus) | set(mems) | set(temps), key=_oid_key):
            cpu = _as_int(cpus.get(suffix))
            mem = _as_int(mems.get(suffix))
            temp = _as_int(temps.get(suffix))
            if not (cpu or mem or temp):
                continue
            entities.append({"index": suffix, "cpu": cpu, "memory": mem,
                             "temperature": temp})
        if not entities:
            raise SnmpError("O agente não expôs hwEntityCpuUsage/MemUsage.")
        return {
            "cpu": max((e["cpu"] for e in entities), default=None) or None,
            "memory": max((e["memory"] for e in entities), default=None) or None,
            "temperature": max((e["temperature"] for e in entities),
                               default=None) or None,
            "entities": entities,
        }


def _as_int(value, default: int = 0) -> int:
    """Converte um valor SNMP em int, tolerando None/END_OF_MIB/texto."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
