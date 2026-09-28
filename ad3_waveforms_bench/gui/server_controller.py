"""ServerController: runs `Ad3Server` in a daemon thread and posts its events to Qt.

Thread model
------------
- The Qt main thread owns all widgets.
- A single daemon thread loads the WaveForms library, runs the server and waits for the stop request;
  the server itself accepts and serves clients on its own threads.
- Server -> Qt: log records go into a `queue.SimpleQueue` through a `logging.Handler`; a `QTimer` on the
  main thread drains the queue and polls the connected client.
- Qt -> server: `stop()` sets a `threading.Event`, which is safe from any thread.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ..remote.wire import DEFAULT_PORT

PACKAGE_LOGGER = "ad3_waveforms_bench"


@dataclass
class ServerConfig:
    port: int = DEFAULT_PORT
    bind_address: str = "127.0.0.1"
    token: str | None = None
    fake: bool = False
    dwf_library: str | None = None
    log_level: str = "INFO"

    @property
    def address(self) -> str:
        return f"{self.bind_address}:{self.port}"


def backend_factory(config: ServerConfig) -> Callable[[], Any]:
    """Load the WaveForms library now (so a missing runtime is reported at start) and return its factory."""
    if config.fake:
        from ..instruments.fake import FakeDwfApi

        fake = FakeDwfApi()
        return lambda: fake
    from ..instruments.dwf import DwfApi, load_library

    api = DwfApi(load_library(config.dwf_library))
    return lambda: api


class _QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.SimpleQueue[logging.LogRecord]) -> None:
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put_nowait(record)


class ServerController(QObject):
    """Manages the server lifecycle from the Qt main thread.

    Signals
    -------
    started(str):
        Emitted with the listening address once the server accepts clients.
    stopped:
        Emitted when the server has shut down (also after a failed start).
    error(str):
        Emitted when the server fails to start or crashes.
    log_record(logging.LogRecord):
        Emitted for each log record of the package.
    client_changed(str):
        Emitted with `host:port` when a client connects and with "" when it leaves.
    """

    started = Signal(str)
    stopped = Signal()
    error = Signal(str)
    log_record = Signal(object)
    client_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._server: Any = None
        self._client = ""
        self._log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        self._queue_handler = _QueueHandler(self._log_queue)

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(100)
        self._drain_timer.timeout.connect(self._poll)
        self._drain_timer.start()

    # ------------------------------------------------------------------
    # Public API (call from the Qt main thread)
    # ------------------------------------------------------------------

    def start(self, config: ServerConfig) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True, name="ad3-bench-server")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def wait(self, timeout: float | None = None) -> None:
        """Block until the server thread has finished (used on quit)."""
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self, config: ServerConfig) -> None:
        package_logger = logging.getLogger(PACKAGE_LOGGER)
        package_logger.setLevel(getattr(logging, config.log_level, logging.INFO))
        package_logger.addHandler(self._queue_handler)
        try:
            self._serve(config)
        finally:
            package_logger.removeHandler(self._queue_handler)

    def _serve(self, config: ServerConfig) -> None:
        from ..remote.server import Ad3Server

        log = logging.getLogger(__name__)
        try:
            factory = backend_factory(config)
            backend = factory()
            version = getattr(backend, "version", None)
            if callable(version):
                log.info("WaveForms runtime %s", version())
            server = Ad3Server(factory, config.bind_address, config.port, config.token)
        except OSError as exc:
            self.error.emit(f"Cannot start: {exc}")
            self.stopped.emit()
            return
        except Exception as exc:
            self.error.emit(f"Unexpected error: {exc}")
            self.stopped.emit()
            return
        try:
            with server:
                self._server = server
                self.started.emit("{}:{}".format(*server.address))
                if config.bind_address not in ("127.0.0.1", "localhost", "::1") and not config.token:
                    log.warning("Listening on %s without a token: anyone who can reach this port controls the AD3", config.bind_address)
                self._stop_event.wait()
        except Exception as exc:
            self.error.emit(f"Server crashed: {exc}")
        finally:
            self._server = None
            self.stopped.emit()

    def _poll(self) -> None:
        while True:
            try:
                record = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_record.emit(record)
        server = self._server
        client = (server.client or "") if server is not None else ""
        if client != self._client:
            self._client = client
            self.client_changed.emit(client)
