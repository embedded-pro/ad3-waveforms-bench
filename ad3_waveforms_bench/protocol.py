"""Pure parsing and formatting of a line-based command protocol.

A command is one line `name [positional...] [key=value...]`. Every command ends with exactly one final
line, `OK [key=value...]` or `ERR <reason>`; asynchronous notifications are `EVT <kind> [key=value...]`
lines that may appear at any time. The tokens are configurable through `Dialect`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

UNRECOGNIZED = "unrecognized"
UNRECOGNIZED_TEXT = "Unrecognized command."

_TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")


class ProtocolError(ValueError):
    """A line or an argument does not follow the protocol."""


@dataclass(frozen=True)
class Dialect:
    """The tokens that frame the protocol.

    `unrecognized` lists whole lines that also end a command, as an error with reason `unrecognized`
    (the EMIL terminal prints `Unrecognized command.` for unknown commands). `line_terminator` ends every
    command line the host writes.
    """

    ok: str = "OK"
    err: str = "ERR"
    event: str = "EVT"
    prompt: str = "> "
    unrecognized: tuple[str, ...] = (UNRECOGNIZED_TEXT,)
    line_terminator: str = "\r"
    default_error_reason: str = "failed"

    def is_final_line(self, line: str) -> bool:
        return _starts_with_token(line, self.ok) or _starts_with_token(line, self.err) or line in self.unrecognized

    def is_event_line(self, line: str) -> bool:
        return line.startswith(self.event + " ")

    def parse_response(self, line: str) -> Response:
        line = line.strip()
        if line in self.unrecognized:
            return Response(raw=line, args=(UNRECOGNIZED,), values={}, ok=False)
        tokens = line.split()
        if not tokens or tokens[0] not in (self.ok, self.err):
            raise ProtocolError(f"not a final line: {line!r}")
        args, values = _split_tokens(tokens[1:])
        return Response(raw=line, args=args, values=values, ok=tokens[0] == self.ok, default_reason=self.default_error_reason)

    def parse_event(self, line: str) -> Event:
        tokens = line.strip().split()
        if len(tokens) < 2 or tokens[0] != self.event:
            raise ProtocolError(f"not an event line: {line!r}")
        args, values = _split_tokens(tokens[2:])
        return Event(raw=line.strip(), args=args, values=values, kind=tokens[1])


DEFAULT_DIALECT = Dialect()


def _starts_with_token(line: str, token: str) -> bool:
    return line == token or line.startswith(token + " ")


def parse_number(text: str) -> int:
    """Decimal, or hexadecimal with a `0x` prefix; a leading minus sign is accepted."""
    negative = text.startswith("-")
    body = text[1:] if negative else text
    try:
        value = int(body, 16) if body.lower().startswith("0x") else int(body, 10)
    except ValueError as error:
        raise ProtocolError(f"not a number: {text!r}") from error
    return -value if negative else value


def parse_hex(text: str) -> bytes:
    if text in ("", "-"):
        return b""
    if len(text) % 2:
        raise ProtocolError(f"odd-length hex string: {text!r}")
    try:
        return bytes.fromhex(text)
    except ValueError as error:
        raise ProtocolError(f"not a hex string: {text!r}") from error


def format_hex(data: bytes | bytearray | Sequence[int]) -> str:
    return bytes(data).hex()


def format_value(value: object) -> str:
    """Render one argument value: bools as 0/1, floats without trailing zeros, sequences comma separated."""
    if isinstance(value, bool):
        text = "1" if value else "0"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = format_decimal(value)
    elif isinstance(value, (bytes, bytearray)):
        text = format_hex(value) if value else "-"
    elif isinstance(value, (list, tuple)):
        text = ",".join(format_value(item) for item in value)
    else:
        text = str(value)
    if not _TOKEN_RE.match(text):
        raise ProtocolError(f"argument must be printable ASCII without spaces: {value!r}")
    return text


def format_decimal(value: float, digits: int = 3) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def format_command(name: str, *positional: object, **options: object) -> str:
    """Build a command line. Options set to None are omitted; a trailing `_` (Python keywords) is dropped."""
    if not _TOKEN_RE.match(name):
        raise ProtocolError(f"bad command name: {name!r}")
    parts = [name]
    parts.extend(format_value(value) for value in positional)
    for key, value in options.items():
        if value is None:
            continue
        parts.append(f"{key.rstrip('_')}={format_value(value)}")
    return " ".join(parts)


def _split_tokens(tokens: Iterable[str]) -> tuple[tuple[str, ...], dict[str, str]]:
    args: list[str] = []
    values: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep and key:
            values[key] = value
        else:
            args.append(token)
    return tuple(args), values


@dataclass(frozen=True)
class _KeyValues:
    raw: str
    args: tuple[str, ...]
    values: dict[str, str]

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def __getitem__(self, key: str) -> str:
        try:
            return self.values[key]
        except KeyError as error:
            raise KeyError(f"{key!r} missing in {self.raw!r}") from error

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def as_int(self, key: str) -> int:
        return parse_number(self[key])

    def as_float(self, key: str) -> float:
        return float(self[key])

    def as_bool(self, key: str) -> bool:
        return self.as_int(key) != 0

    def as_bytes(self, key: str) -> bytes:
        return parse_hex(self[key])

    def as_list(self, key: str) -> list[str]:
        text = self[key]
        return text.split(",") if text else []

    def as_ints(self, key: str) -> list[int]:
        return [parse_number(item) for item in self.as_list(key)]


@dataclass(frozen=True)
class Response(_KeyValues):
    ok: bool = True
    default_reason: str = field(default="failed", compare=False, repr=False)

    @property
    def reason(self) -> str | None:
        if self.ok:
            return None
        return self.args[0] if self.args else self.default_reason


@dataclass(frozen=True)
class Event(_KeyValues):
    kind: str = ""
    timestamp: float = field(default_factory=time.monotonic, compare=False)


def is_final_line(line: str, dialect: Dialect = DEFAULT_DIALECT) -> bool:
    return dialect.is_final_line(line)


def is_event_line(line: str, dialect: Dialect = DEFAULT_DIALECT) -> bool:
    return dialect.is_event_line(line)


def parse_response(line: str, dialect: Dialect = DEFAULT_DIALECT) -> Response:
    return dialect.parse_response(line)


def parse_event(line: str, dialect: Dialect = DEFAULT_DIALECT) -> Event:
    return dialect.parse_event(line)
