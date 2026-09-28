"""`ad3-bench-server`: share the WaveForms library of this machine over TCP.

Run it on the machine the AD3 is plugged into (for example Windows with the WaveForms runtime), and point
the benches in a container at it:

    ad3-bench-server
    AD3_REMOTE=host.docker.internal:5025 pytest --ad3-remote host.docker.internal:5025

One client at a time owns the library. When it disconnects, every device it left open is returned to the
safe state (outputs released, supplies off) and closed. Serial ports of the device under test are not
forwarded here; use a byte-stream bridge such as port-bridge and open it as `socket://host:port`.
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import logging
import os
import signal
import socket
import threading
from collections.abc import Callable
from ctypes import c_int
from typing import Any

from .wire import DEFAULT_PORT, PROTOCOL_VERSION, Decoded, WireError, encode_constants, read_frame, write_frame

log = logging.getLogger(__name__)


def _listen(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    server = socket.socket(family, socket.SOCK_STREAM)
    if os.name != "nt":
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(4)
    server.settimeout(0.2)  # closing a socket does not wake a blocked accept() everywhere; poll instead
    return server


class Ad3Server:
    """Forwards `FDwf*` calls to `backend_factory()` (the local `DwfApi`, or `FakeDwfApi` for tests).

    Clients are accepted on a background thread and served one at a time; the others are turned away.
    """

    def __init__(
        self,
        backend_factory: Callable[[], Any],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        token: str | None = None,
    ) -> None:
        self._listener = _listen(host, port)
        self._busy = threading.Lock()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._clients: set[socket.socket] = set()
        self._backend_factory = backend_factory
        self._backend: Any = None
        self._token = token
        self.client: str | None = None
        """`host:port` of the connected client, None when idle."""

    @property
    def backend(self) -> Any:
        if self._backend is None:
            self._backend = self._backend_factory()
        return self._backend

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._listener.getsockname()[:2]
        return host, port

    def start(self) -> None:
        self._thread = threading.Thread(target=self._accept_loop, name="dwf-accept", daemon=True)
        self._thread.start()
        log.info("listening on %s:%d", *self.address)

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(OSError):
            self._listener.close()
        for client in list(self._clients):
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> Ad3Server:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                client, peer = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client.settimeout(None)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not self._busy.acquire(blocking=False):
                log.warning("refusing %s:%d, a client is already connected", *peer[:2])
                self._refuse(client)
                continue
            threading.Thread(target=self._run_client, args=(client, peer), name="dwf-client", daemon=True).start()

    def _run_client(self, client: socket.socket, peer: Any) -> None:
        self._clients.add(client)
        self.client = "{}:{}".format(*peer[:2])
        log.info("client %s:%d connected", *peer[:2])
        try:
            self.serve(client)
        except Exception:
            log.exception("client %s:%d failed", *peer[:2])
        finally:
            self._clients.discard(client)
            with contextlib.suppress(OSError):
                client.close()
            log.info("client %s:%d disconnected", *peer[:2])
            self.client = None
            self._busy.release()

    def _refuse(self, client: socket.socket) -> None:
        # Read the hello first: closing with unread data would reset the connection before the reply arrives.
        client.settimeout(2.0)
        with contextlib.suppress(OSError, WireError), client.makefile("rwb") as stream:
            message = read_frame(stream)
            write_frame(stream, {"id": (message or {}).get("id"), "ok": False, "error": "busy: another client is using this AD3 server"})
        client.close()

    def serve(self, client: socket.socket) -> None:
        handles: set[int] = set()
        with client.makefile("rwb") as stream:
            try:
                if not self._handshake(stream):
                    return
                while True:
                    message = read_frame(stream)
                    if message is None:
                        return
                    write_frame(stream, self._handle(message, handles))
            except (OSError, WireError) as error:
                log.warning("connection dropped: %s", error)
            finally:
                self._release(handles)

    def _handshake(self, stream: Any) -> bool:
        message = read_frame(stream)
        if message is None:
            return False
        reply: dict[str, Any] = {"id": message.get("id"), "ok": False}
        if message.get("op") != "hello":
            reply["error"] = "the first request must be hello"
        elif message.get("protocol") != PROTOCOL_VERSION:
            reply["error"] = f"protocol {message.get('protocol')} is not supported (server speaks {PROTOCOL_VERSION})"
        elif self._token and not hmac.compare_digest(str(message.get("token") or ""), self._token):
            reply["error"] = "invalid token"
        else:
            try:
                backend = self.backend
            except Exception as error:
                reply["error"] = f"WaveForms library unavailable on the server: {error}"
            else:
                version = getattr(backend, "version", None)
                reply.update(ok=True, protocol=PROTOCOL_VERSION, constants=encode_constants(backend.constants))
                with contextlib.suppress(Exception):
                    reply["version"] = version() if callable(version) else ""
        write_frame(stream, reply)
        return bool(reply["ok"])

    def _handle(self, message: dict[str, Any], handles: set[int]) -> dict[str, Any]:
        reply: dict[str, Any] = {"id": message.get("id")}
        op = message.get("op")
        if op == "last_error":
            last_error = getattr(self.backend, "last_error", None)
            return {**reply, "ok": True, "error_message": last_error() if callable(last_error) else ""}
        if op != "call":
            return {**reply, "ok": False, "error": f"unknown op {op!r}"}
        name = message.get("name")
        if not isinstance(name, str) or not name.startswith("FDwf") or not name.isidentifier():
            return {**reply, "ok": False, "error": f"not a WaveForms function: {name!r}"}
        try:
            decoded = Decoded(list(message.get("args") or []))
        except (WireError, TypeError, ValueError, KeyError) as error:
            return {**reply, "ok": False, "error": f"{name}: bad arguments: {error}"}
        try:
            result = getattr(self.backend, name)(*decoded.call_args())
        except Exception as error:
            return {**reply, "ok": False, "error": str(error) or type(error).__name__}
        self._track(name, decoded, handles)
        return {**reply, "ok": True, "result": int(result), "out": decoded.outputs()}

    @staticmethod
    def _track(name: str, decoded: Decoded, handles: set[int]) -> None:
        objects = decoded.objects
        if name == "FDwfDeviceOpen" and len(objects) >= 2 and objects[1] is not None:
            handles.add(int(objects[1].value))
        elif name == "FDwfDeviceConfigOpen" and len(objects) >= 3 and objects[2] is not None:
            handles.add(int(objects[2].value))
        elif name == "FDwfDeviceClose" and objects:
            handles.discard(int(objects[0].value))
        elif name == "FDwfDeviceCloseAll":
            handles.clear()

    def _release(self, handles: set[int]) -> None:
        """Return every device the client left open to the safe state and close it."""
        from ..instruments.ad3 import AnalogDiscovery3

        for handle in sorted(handles):
            log.warning("client left device handle %d open; releasing outputs and closing it", handle)
            device = AnalogDiscovery3(api_factory=lambda: self.backend)
            device.api = self.backend
            device.handle = c_int(handle)
            try:
                device.close()
            except Exception:
                log.exception("failed to close device handle %d cleanly", handle)
        handles.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ad3-bench-server",
        description="Share this machine's WaveForms library (Analog Discovery 3) over TCP.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1; 0.0.0.0 for every interface)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port of the WaveForms link (default {DEFAULT_PORT})")
    parser.add_argument(
        "--token", default=os.environ.get("AD3_SERVER_TOKEN"), help="shared secret clients must send (default AD3_SERVER_TOKEN)"
    )
    parser.add_argument("--fake", action="store_true", help="serve the in-memory FakeDwfApi instead of the WaveForms library")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
        log.warning("listening on %s without --token: anyone who can reach this port controls the AD3", args.host)

    if args.fake:
        from ..instruments.fake import FakeDwfApi

        factory: Callable[[], Any] = FakeDwfApi
    else:
        from ..instruments.dwf import DwfApi

        factory = DwfApi

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with contextlib.suppress(AttributeError, ValueError):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with Ad3Server(factory, args.host, args.port, args.token):
        while not stop.wait(0.5):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
