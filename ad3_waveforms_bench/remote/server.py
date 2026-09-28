"""`ad3-bench-server`: share the WaveForms library and serial ports of this machine over TCP.

Run it on the machine the AD3 and the device under test are plugged into (for example Windows with the
WaveForms runtime), and point the benches in a container at it:

    ad3-bench-server --serial COM5=4000
    AD3_REMOTE=host.docker.internal:5025 pytest --ad3-remote host.docker.internal:5025

One client at a time owns the library. When it disconnects, every device it left open is returned to the
safe state (outputs released, supplies off) and closed. `--serial PORT=TCP` serves a serial port over
RFC2217 (`rfc2217://host:TCP` in pyserial), so the client also sets the baud rate.
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
import time
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


class _TcpService:
    """Accepts clients on a background thread and serves one at a time; the others are turned away."""

    name = "service"

    def __init__(self, host: str, port: int) -> None:
        self._listener = _listen(host, port)
        self._busy = threading.Lock()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._clients: set[socket.socket] = set()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._listener.getsockname()[:2]
        return host, port

    def start(self) -> None:
        self._thread = threading.Thread(target=self._accept_loop, name=f"{self.name}-accept", daemon=True)
        self._thread.start()
        log.info("%s listening on %s:%d", self.name, *self.address)

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(OSError):
            self._listener.close()
        for client in list(self._clients):
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> _TcpService:
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
                log.warning("%s: refusing %s:%d, a client is already connected", self.name, *peer[:2])
                self._refuse(client)
                continue
            threading.Thread(target=self._run_client, args=(client, peer), name=f"{self.name}-client", daemon=True).start()

    def _run_client(self, client: socket.socket, peer: Any) -> None:
        self._clients.add(client)
        log.info("%s: client %s:%d connected", self.name, *peer[:2])
        try:
            self.serve(client)
        except Exception:
            log.exception("%s: client %s:%d failed", self.name, *peer[:2])
        finally:
            self._clients.discard(client)
            with contextlib.suppress(OSError):
                client.close()
            log.info("%s: client %s:%d disconnected", self.name, *peer[:2])
            self._busy.release()

    def _refuse(self, client: socket.socket) -> None:
        client.close()

    def serve(self, client: socket.socket) -> None:
        raise NotImplementedError


class Ad3Server(_TcpService):
    """Forwards `FDwf*` calls to `backend_factory()` (the local `DwfApi`, or `FakeDwfApi` for tests)."""

    name = "dwf"

    def __init__(
        self,
        backend_factory: Callable[[], Any],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        token: str | None = None,
    ) -> None:
        super().__init__(host, port)
        self._backend_factory = backend_factory
        self._backend: Any = None
        self._token = token

    @property
    def backend(self) -> Any:
        if self._backend is None:
            self._backend = self._backend_factory()
        return self._backend

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
                log.warning("dwf: connection dropped: %s", error)
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
            log.warning("dwf: client left device handle %d open; releasing outputs and closing it", handle)
            device = AnalogDiscovery3(api_factory=lambda: self.backend)
            device.api = self.backend
            device.handle = c_int(handle)
            try:
                device.close()
            except Exception:
                log.exception("dwf: failed to close device handle %d cleanly", handle)
        handles.clear()


class SerialBridge(_TcpService):
    """Serves the serial port `url` (`COM5`, `/dev/ttyACM0`, `loop://`) over RFC2217 to one client at a time.

    The port is opened when a client connects and closed when it leaves, so other tools can use it in between.
    """

    name = "serial"

    def __init__(self, url: str, host: str = "127.0.0.1", port: int = 4000) -> None:
        super().__init__(host, port)
        self.url = url
        self.name = f"serial {url}"

    def serve(self, client: socket.socket) -> None:
        import serial
        import serial.rfc2217

        try:
            port = serial.serial_for_url(self.url, timeout=0.05)
        except serial.SerialException as error:
            log.error("%s: cannot open the port: %s", self.name, error)
            return
        write_lock = threading.Lock()
        alive = threading.Event()
        alive.set()

        class _Connection:
            @staticmethod
            def write(data: bytes) -> None:
                with write_lock:
                    client.sendall(data)

        # The stubs expect an rfc2217.Serial; any pyserial port and any object with `write` work.
        manager = serial.rfc2217.PortManager(port, _Connection(), logger=logging.getLogger(f"{__name__}.rfc2217"))  # type: ignore[arg-type]

        def serial_to_socket() -> None:
            last_poll = time.monotonic()
            while alive.is_set():
                try:
                    data = port.read(port.in_waiting or 1)
                    if data:
                        _Connection.write(b"".join(manager.escape(data)))
                    if time.monotonic() - last_poll >= 1.0:
                        manager.check_modem_lines()
                        last_poll = time.monotonic()
                except (OSError, serial.SerialException) as error:
                    log.warning("%s: %s", self.name, error)
                    break
            alive.clear()
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)

        reader = threading.Thread(target=serial_to_socket, name=f"{self.name}-reader", daemon=True)
        reader.start()
        try:
            while alive.is_set():
                try:
                    data = client.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                port.write(b"".join(manager.filter(data)))
        finally:
            alive.clear()
            reader.join(timeout=2)
            port.close()


def parse_serial_spec(spec: str) -> tuple[str, int]:
    """`COM5=4000` -> (`COM5`, 4000)."""
    url, sep, port = spec.rpartition("=")
    if not sep or not url or not port.isdigit():
        raise argparse.ArgumentTypeError(f"expected PORT=TCP_PORT, got {spec!r}")
    return url, int(port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ad3-bench-server",
        description="Share this machine's WaveForms library (Analog Discovery 3) and serial ports over TCP.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1; 0.0.0.0 for every interface)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port of the WaveForms link (default {DEFAULT_PORT})")
    parser.add_argument(
        "--token", default=os.environ.get("AD3_SERVER_TOKEN"), help="shared secret WaveForms clients must send (default AD3_SERVER_TOKEN)"
    )
    parser.add_argument(
        "--serial",
        action="append",
        default=[],
        type=parse_serial_spec,
        metavar="PORT=TCP",
        help="serve a serial port over RFC2217, e.g. COM5=4000 (repeatable)",
    )
    parser.add_argument("--no-dwf", action="store_true", help="only serve the serial ports")
    parser.add_argument("--fake", action="store_true", help="serve the in-memory FakeDwfApi instead of the WaveForms library")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger(f"{__name__}.rfc2217").setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
        log.warning("listening on %s without --token: anyone who can reach this port controls the AD3", args.host)

    services: list[_TcpService] = []
    if not args.no_dwf:
        if args.fake:
            from ..instruments.fake import FakeDwfApi

            factory: Callable[[], Any] = FakeDwfApi
        else:
            from ..instruments.dwf import DwfApi

            factory = DwfApi
        services.append(Ad3Server(factory, args.host, args.port, args.token))
    services.extend(SerialBridge(url, args.host, port) for url, port in args.serial)
    if not services:
        parser.error("nothing to serve")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with contextlib.suppress(AttributeError, ValueError):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    for service in services:
        service.start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        for service in services:
            service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
