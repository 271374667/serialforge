"""Enumerations shared by the serialforge public API."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class CommandStatus(StrEnum):
    """Terminal status reported for a command."""

    OK = "ok"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    CANCELLED = "cancelled"
    PARSE_ERROR = "parse_error"
    DEVICE_ERROR = "device_error"
    BUSY = "busy"


class ConnectionState(StrEnum):
    """Connection lifecycle state."""

    DISCONNECTED = "disconnected"
    PROBING = "probing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class ResponseMode(StrEnum):
    """Expected response shape for a command."""

    NO_REPLY = "no_reply"
    SINGLE = "single"
    MULTI = "multi"
    STREAM = "stream"


class Correlation(StrEnum):
    """How responses are associated with in-flight commands."""

    EXCLUSIVE = "exclusive"
    TAGGED = "tagged"


class ScanMode(StrEnum):
    """Device scan completion policy."""

    ALL = "all"
    FIRST_MATCH = "first_match"


class DataBits(IntEnum):
    """Number of data bits in a serial frame."""

    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8


class Parity(StrEnum):
    """Serial parity mode."""

    NONE = "N"
    EVEN = "E"
    ODD = "O"
    MARK = "M"
    SPACE = "S"


class StopBits(StrEnum):
    """Number of stop bits."""

    ONE = "1"
    ONE_POINT_FIVE = "1.5"
    TWO = "2"


class FlowControl(StrEnum):
    """Serial flow-control mode."""

    NONE = "none"
    XON_XOFF = "xon_xoff"
    RTS_CTS = "rts_cts"
    DSR_DTR = "dsr_dtr"


class FramingMode(StrEnum):
    """Byte framing strategy."""

    LINE = "line"
    DELIMITED = "delimited"
    LENGTH_PREFIX = "length_prefix"
    SILENCE_GAP = "silence_gap"


class Checksum(StrEnum):
    """Checksum algorithm selected for binary frames."""

    NONE = "none"
    SUM8 = "sum8"
    XOR = "xor"
    CRC16_MODBUS = "crc16_modbus"
    CRC16_CCITT = "crc16_ccitt"
    CRC32 = "crc32"


class CommandPriority(IntEnum):
    """Priority used by the command queue."""

    NORMAL = 0
    HIGH = 1
    URGENT = 2


class SendRoute(StrEnum):
    """How a send target was resolved."""

    SPEC = "spec"
    MATCHED = "matched"
    RAW = "raw"


class TimeoutPolicy(StrEnum):
    """Special timeout policies accepted by command declarations."""

    AUTO = "auto"
