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
# Exceções de varbind (SNMPv2) na posição do valor
_NO_SUCH_OBJECT = 0x80
_NO_SUCH_INSTANCE = 0x81
_END_OF_MIB_VIEW = 0x82

_SNMP_VERSION_2C = 1   # 0 = v1, 1 = v2c


class SnmpError(Exception):
    pass


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

    # -- baixo nível ------------------------------------------------------- #
    def _next_id(self) -> int:
        self._request_id = (self._request_id + 1) & 0x7FFFFFFF
        return self._request_id or 1

    def _build_message(self, pdu_tag: int, oids: list[str],
                       request_id: int) -> bytes:
        varbinds = b""
        for oid in oids:
            varbinds += _tlv(_SEQUENCE,
                             _encode_oid(oid) + _tlv(_NULL, b""))
        varbind_list = _tlv(_SEQUENCE, varbinds)
        pdu = _tlv(pdu_tag,
                   _encode_integer(request_id)
                   + _encode_integer(0)          # error-status
                   + _encode_integer(0)          # error-index
                   + varbind_list)
        message = _tlv(_SEQUENCE,
                       _encode_integer(_SNMP_VERSION_2C)
                       + _tlv(_OCTET_STRING, self.community.encode())
                       + pdu)
        return message

    def _transact(self, pdu_tag: int, oids: list[str]) -> list[tuple]:
        request_id = self._next_id()
        message = self._build_message(pdu_tag, oids, request_id)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            last_exc: Exception | None = None
            for _ in range(self.retries + 1):
                try:
                    sock.sendto(message, (self.host, self.port))
                    data, _addr = sock.recvfrom(65535)
                    return self._parse_response(data, request_id)
                except socket.timeout as exc:
                    last_exc = exc
                    continue
            raise SnmpError(
                f"Sem resposta SNMP de {self.host}:{self.port} "
                f"(timeout {self.timeout:.0f}s). Verifique a comunidade e se "
                f"o agente está habilitado.") from last_exc
        except OSError as exc:
            raise SnmpError(f"Erro de rede SNMP: {exc}") from exc
        finally:
            sock.close()

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
        _, erridx_bytes, pidx = _read_tlv(pdu, pidx)
        error_status = int.from_bytes(err_bytes, "big", signed=True)
        if error_status:
            raise SnmpError(
                f"Agente retornou erro SNMP (error-status={error_status}).")
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
