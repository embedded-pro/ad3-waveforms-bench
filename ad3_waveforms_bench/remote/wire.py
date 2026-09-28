"""Wire format of the remote WaveForms link: ctypes arguments to JSON and back, newline-delimited frames.

An `FDwf*` call is forwarded with its ctypes arguments as they are:

- `None` -> `null`
- a simple ctypes value -> `{"t": "i4", "v": 5}` (kind `i`/`u`/`f`/`c`/`b` and size in bytes, so the
  meaning does not depend on the platform's `long`)
- `byref(x)` -> `{"ref": <x>}`
- an array (`(c_uint16 * n)()`, `create_string_buffer(n)`) -> `{"arr": "u2", "n": n, "b": <base64 or null for all zeros>}`

The server rebuilds fresh ctypes objects, calls the library and returns what the call wrote: the value of
every `byref` simple value and the bytes of every array that changed. The client writes those back into
its own objects, so out-parameters behave as with a local library.
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_PORT = 5025
MAX_FRAME = 64 * 1024 * 1024

_TYPES: dict[str, Any] = {
    "i1": ctypes.c_int8,
    "u1": ctypes.c_uint8,
    "i2": ctypes.c_int16,
    "u2": ctypes.c_uint16,
    "i4": ctypes.c_int32,
    "u4": ctypes.c_uint32,
    "i8": ctypes.c_int64,
    "u8": ctypes.c_uint64,
    "f4": ctypes.c_float,
    "f8": ctypes.c_double,
    "c1": ctypes.c_char,
    "b1": ctypes.c_bool,
}


class WireError(ValueError):
    """A frame or an argument does not follow the wire format."""


def type_code(ctype: Any) -> str:
    """`i4`, `u2`, `f8`, ... for a simple ctypes type."""
    code = getattr(ctype, "_type_", None)
    if not isinstance(code, str):
        raise TypeError(f"{ctype!r} is not a simple ctypes type")
    size = ctypes.sizeof(ctype)
    if code == "c":
        kind = "c"
    elif code == "?":
        kind = "b"
    elif code in "fdg":
        kind = "f"
    elif code in "bhilq":
        kind = "i"
    elif code in "BHILQ":
        kind = "u"
    else:
        raise TypeError(f"ctypes type {ctype.__name__} cannot be forwarded")
    name = f"{kind}{size}"
    if name not in _TYPES:
        raise TypeError(f"ctypes type {ctype.__name__} ({name}) cannot be forwarded")
    return name


def ctype_of(code: str) -> Any:
    try:
        return _TYPES[code]
    except KeyError as error:
        raise WireError(f"unknown type code {code!r}") from error


def _is_byref(arg: Any) -> bool:
    return type(arg).__name__ == "CArgObject" and hasattr(arg, "_obj")


def _raw(array: ctypes.Array[Any]) -> bytes:
    return ctypes.string_at(ctypes.addressof(array), ctypes.sizeof(array))


def _encode_bytes(raw: bytes) -> str | None:
    return base64.b64encode(raw).decode("ascii") if any(raw) else None


def _encode_simple_value(value: Any) -> Any:
    return value[0] if isinstance(value, bytes) else value


def _encode_object(obj: Any) -> dict[str, Any]:
    if isinstance(obj, ctypes.Array):
        return {"arr": type_code(obj._type_), "n": len(obj), "b": _encode_bytes(_raw(obj))}
    if isinstance(obj, ctypes._SimpleCData):
        return {"t": type_code(type(obj)), "v": _encode_simple_value(obj.value)}
    raise TypeError(f"{type(obj).__name__} cannot be forwarded")


def encode_arg(arg: Any) -> Any:
    if arg is None:
        return None
    if _is_byref(arg):
        return {"ref": _encode_object(arg._obj)}
    return _encode_object(arg)


def _decode_object(obj: Any) -> Any:
    if not isinstance(obj, dict):
        raise WireError(f"bad argument {obj!r}")
    if "arr" in obj:
        element = ctype_of(obj["arr"])
        length = int(obj["n"])
        if length < 0 or length * ctypes.sizeof(element) > MAX_FRAME:
            raise WireError(f"bad array length {length}")
        array = (element * length)()
        if obj.get("b"):
            raw = base64.b64decode(obj["b"])
            if len(raw) != ctypes.sizeof(array):
                raise WireError("array size does not match its data")
            ctypes.memmove(array, raw, len(raw))
        return array
    if "t" in obj:
        ctype = ctype_of(obj["t"])
        value = obj["v"]
        return ctype(bytes([value])) if obj["t"] == "c1" else ctype(value)
    raise WireError(f"bad argument {obj!r}")


class Decoded:
    """Server side: the ctypes objects rebuilt from one call's arguments."""

    def __init__(self, args: list[Any]) -> None:
        self.objects: list[Any] = []
        self.by_ref: list[bool] = []
        self.initial: list[bytes | None] = []
        for arg in args:
            if arg is None:
                self.objects.append(None)
                self.by_ref.append(False)
            elif isinstance(arg, dict) and "ref" in arg:
                self.objects.append(_decode_object(arg["ref"]))
                self.by_ref.append(True)
            else:
                self.objects.append(_decode_object(arg))
                self.by_ref.append(False)
            obj = self.objects[-1]
            self.initial.append(_raw(obj) if isinstance(obj, ctypes.Array) else None)

    def call_args(self) -> list[Any]:
        return [ctypes.byref(obj) if ref else obj for obj, ref in zip(self.objects, self.by_ref)]

    def outputs(self) -> list[Any]:
        """What the call wrote: the value of each byref simple value, the bytes of each changed array."""
        result: list[Any] = []
        for obj, ref, initial in zip(self.objects, self.by_ref, self.initial):
            if isinstance(obj, ctypes.Array):
                raw = _raw(obj)
                result.append(None if raw == initial else {"b": base64.b64encode(raw).decode("ascii")})
            elif ref and obj is not None:
                result.append({"v": _encode_simple_value(obj.value)})
            else:
                result.append(None)
        return result


def apply_outputs(args: tuple[Any, ...], outputs: list[Any]) -> None:
    """Client side: write the server's outputs back into the caller's ctypes objects."""
    if len(outputs) != len(args):
        raise WireError("output count does not match the arguments")
    for arg, output in zip(args, outputs):
        if output is None:
            continue
        target = arg._obj if _is_byref(arg) else arg
        if isinstance(target, ctypes.Array):
            raw = base64.b64decode(output["b"])
            if len(raw) != ctypes.sizeof(target):
                raise WireError("returned array size does not match")
            ctypes.memmove(target, raw, len(raw))
        else:
            value = output["v"]
            target.value = bytes([value]) if isinstance(target, ctypes.c_char) else value


def encode_constants(constants: Any) -> dict[str, Any]:
    return {name: encode_arg(value) for name, value in vars(constants).items() if isinstance(value, ctypes._SimpleCData)}


def decode_constants(encoded: dict[str, Any]) -> dict[str, Any]:
    return {name: _decode_object(value) for name, value in encoded.items()}


def write_frame(stream: io.BufferedIOBase, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")
    stream.flush()


def read_frame(stream: io.BufferedIOBase) -> dict[str, Any] | None:
    """The next message, or None when the peer closed the connection."""
    line = stream.readline(MAX_FRAME + 1)
    if not line:
        return None
    if len(line) > MAX_FRAME or not line.endswith(b"\n"):
        raise WireError("frame too long or truncated")
    try:
        message = json.loads(line)
    except ValueError as error:
        raise WireError(f"bad frame: {error}") from error
    if not isinstance(message, dict):
        raise WireError("a frame must be a JSON object")
    return message


def parse_address(address: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """`host`, `host:port` or `[v6addr]:port`."""
    address = address.strip()
    if not address:
        raise ValueError("empty address")
    if address.startswith("["):
        host, _, rest = address[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    if address.count(":") == 1:
        host, port = address.split(":")
        return host, int(port)
    return address, default_port
