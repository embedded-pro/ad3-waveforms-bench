"""`RemoteDwfApi`: the WaveForms library of another machine (an `ad3-bench-server`), over TCP."""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from ..instruments.dwf import DwfError
from .wire import PROTOCOL_VERSION, WireError, apply_outputs, decode_constants, encode_arg, parse_address, read_frame, write_frame

log = logging.getLogger(__name__)


class RemoteError(ConnectionError):
    """The server refused the session or the link broke."""


class RemoteDwfApi:
    """Drop-in for `DwfApi`: `FDwf*` calls run on the server, out-parameters are written back.

    `address` is `host[:port]` (default port 5025). The constants come from the server, so they match
    its WaveForms runtime. Use it as `AnalogDiscovery3(remote="host:5025")` or through `api_factory`.
    """

    def __init__(self, address: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.address = parse_address(address)
        self._lock = threading.Lock()
        self._next_id = 0
        self._socket = socket.create_connection(self.address, timeout=timeout)
        try:
            self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._stream = self._socket.makefile("rwb")
            hello = self._request({"op": "hello", "protocol": PROTOCOL_VERSION, "token": token})
        except BaseException:
            self._socket.close()
            raise
        self.constants = SimpleNamespace(**decode_constants(hello.get("constants", {})))
        self._version = str(hello.get("version", ""))
        log.info("connected to %s:%d (WaveForms %s)", *self.address, self._version)

    def __enter__(self) -> RemoteDwfApi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._stream.closed:
                return
            try:
                self._stream.close()
            finally:
                self._socket.close()

    def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._stream.closed:
                raise RemoteError("the remote WaveForms link is closed")
            self._next_id += 1
            message["id"] = self._next_id
            try:
                write_frame(self._stream, message)
                reply = read_frame(self._stream)
            except (OSError, WireError) as error:
                raise RemoteError(f"remote WaveForms link to {self.address[0]}:{self.address[1]} failed: {error}") from error
            if reply is None:
                raise RemoteError(f"{self.address[0]}:{self.address[1]} closed the connection")
        if reply.get("id") not in (None, message["id"]):
            raise RemoteError(f"out-of-order reply {reply.get('id')} to request {message['id']}")
        if not reply.get("ok"):
            reason = str(reply.get("error", "unknown error"))
            if message["op"] == "call":
                raise DwfError(reason)
            raise RemoteError(reason)
        return reply

    def __getattr__(self, name: str) -> Callable[..., int]:
        if not name.startswith("FDwf"):
            raise AttributeError(name)

        def call(*args: Any) -> int:
            reply = self._request({"op": "call", "name": name, "args": [encode_arg(arg) for arg in args]})
            apply_outputs(args, reply.get("out", []))
            return int(reply.get("result", 1))

        return call

    def last_error(self) -> str:
        return str(self._request({"op": "last_error"}).get("error_message", ""))

    def version(self) -> str:
        return self._version
