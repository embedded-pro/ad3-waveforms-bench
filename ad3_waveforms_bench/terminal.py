"""Line framing over a serial command terminal such as EMIL's `services::TerminalWithCommandsImpl`.

The terminal on the device echoes input and prints a prompt; `FirmwareTerminal` strips both, returns the
final `OK`/`ERR` line of each command and queues `EVT` lines. The tokens come from a `Dialect`.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .protocol import DEFAULT_DIALECT, Dialect, Event, Response

DEFAULT_BAUD = 115200
PROMPT = DEFAULT_DIALECT.prompt
CTRL_C = b"\x03"

log = logging.getLogger(__name__)

_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|O.|[@-Z\\-_])")
_INCOMPLETE_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*|O)?$")


class SerialLike(Protocol):
    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def close(self) -> None: ...

    @property
    def in_waiting(self) -> int: ...


class TerminalError(Exception):
    pass


class TerminalTimeout(TerminalError, TimeoutError):
    def __init__(self, what: str, timeout: float) -> None:
        super().__init__(f"no answer to {what!r} within {timeout:.3g} s")
        self.what = what
        self.timeout = timeout


class FirmwareError(TerminalError):
    """The device answered `ERR <reason>` (or did not recognise the command)."""

    def __init__(self, reason: str, command: str, response: Response | None = None) -> None:
        super().__init__(f"{command!r} failed: ERR {reason}")
        self.reason = reason
        self.command = command
        self.response = response


def clean_line(raw: str, prompt: str = PROMPT) -> str:
    """Remove ANSI/escape sequences, bells and prompts, and apply backspaces."""
    text = _ESCAPE_RE.sub("", raw).replace("\a", "").replace("\x00", "")
    chars: list[str] = []
    for char in text:
        if char in "\b\x7f":
            if chars:
                chars.pop()
        elif char >= " " or char == "\t":
            chars.append(char)
    text = "".join(chars)
    while prompt and (text.startswith(prompt) or text == prompt.strip()):
        text = text[len(prompt) :]
    return text.strip()


class LineAssembler:
    """Turns the raw terminal byte stream into protocol lines.

    The EMIL terminal echoes input, prints `\\r\\n` on enter, and prints the prompt `> ` without a newline
    after each command, so a final line may be followed by the prompt instead of a line break
    (`OK> `). An event printed with `Tracer::Trace()` starts with `\\r\\n` and may not be terminated at all
    until the next output, so an unterminated protocol line is also released after `idle_flush` seconds
    without new bytes.
    """

    def __init__(self, idle_flush: float = 0.05, dialect: Dialect = DEFAULT_DIALECT) -> None:
        self.idle_flush = idle_flush
        self.dialect = dialect
        self._buffer = ""
        self._last_data = time.monotonic()

    def feed(self, data: bytes, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        if data:
            self._buffer += data.decode("latin-1")
            self._last_data = now
        lines: list[str] = []
        while True:
            match = re.search(r"[\r\n]", self._buffer)
            if not match:
                break
            raw, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
            line = clean_line(raw, self.dialect.prompt)
            if line:
                lines.append(line)
        tail = self._flush_tail(now)
        if tail:
            lines.append(tail)
        return lines

    def poll(self, now: float | None = None) -> list[str]:
        return self.feed(b"", now)

    def _flush_tail(self, now: float) -> str | None:
        if not self._buffer or _INCOMPLETE_ESCAPE_RE.search(self._buffer):
            return None
        prompt = self.dialect.prompt
        prompted = bool(prompt) and self._buffer.endswith(prompt)
        line = clean_line(self._buffer[: -len(prompt)] if prompted else self._buffer, prompt)
        protocol_line = self.dialect.is_final_line(line) or self.dialect.is_event_line(line)
        if prompted and not line:
            self._buffer = ""
            return None
        if protocol_line and (prompted or now - self._last_data >= self.idle_flush):
            self._buffer = ""
            return line
        return None


@dataclass
class PendingCommand:
    """A command written to the device whose final line has not been read yet."""

    terminal: FirmwareTerminal
    line: str
    timeout: float

    def wait(self, timeout: float | None = None, check: bool = True) -> Response:
        return self.terminal.finish(self, timeout, check)


class FirmwareTerminal:
    """Sends one command at a time and returns its final `OK`/`ERR` line; `EVT` lines are queued."""

    def __init__(
        self,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        timeout: float = 2.0,
        serial: SerialLike | None = None,
        max_command_length: int = 255,
        idle_flush: float = 0.05,
        event_capacity: int = 4096,
        dialect: Dialect | None = None,
    ) -> None:
        if serial is None:
            if port is None:
                raise ValueError("either port or serial is required")
            import serial as pyserial

            # pyserial's RFC2217 client rejects write timeouts
            write_timeout = None if port.startswith("rfc2217://") else timeout
            serial = pyserial.serial_for_url(port, baudrate=baud, timeout=0.01, write_timeout=write_timeout)
        self._serial = serial
        self.port = port
        self.timeout = timeout
        self.max_command_length = max_command_length
        self.dialect = dialect or DEFAULT_DIALECT
        self._assembler = LineAssembler(idle_flush, self.dialect)
        self._finals: deque[Response] = deque()
        self._events: deque[Event] = deque(maxlen=event_capacity)
        self.other_lines: deque[str] = deque(maxlen=200)
        self.history: deque[str] = deque(maxlen=200)
        self._pending: PendingCommand | None = None

    def __enter__(self) -> FirmwareTerminal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._serial.close()

    def command(self, line: str, timeout: float | None = None, check: bool = True) -> Response:
        return self.finish(self.begin(line, timeout), timeout, check)

    def begin(self, line: str, timeout: float | None = None) -> PendingCommand:
        """Write a command without waiting for its final line (e.g. a `uart.send` blocked by CTS)."""
        self._validate(line)
        if self._pending is not None:
            raise TerminalError(f"{self._pending.line!r} is still pending")
        self.pump()
        while self._finals:
            log.warning("discarding stale final line %r", self._finals.popleft().raw)
        log.debug("> %s", line)
        self.history.append(f"> {line}")
        self._serial.write((line + self.dialect.line_terminator).encode("ascii"))
        self._pending = PendingCommand(self, line, self.timeout if timeout is None else timeout)
        return self._pending

    def finish(self, pending: PendingCommand, timeout: float | None = None, check: bool = True) -> Response:
        if pending is not self._pending:
            raise TerminalError(f"{pending.line!r} is not the pending command")
        limit = pending.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while not self._finals:
            if time.monotonic() >= deadline:
                self._pending = None
                raise TerminalTimeout(pending.line, limit)
            self.pump()
        self._pending = None
        response = self._finals.popleft()
        if check and not response.ok:
            raise FirmwareError(response.reason or self.dialect.default_error_reason, pending.line, response)
        return response

    def write_raw(self, data: bytes) -> None:
        self._serial.write(data)

    def send_nowait(self, line: str) -> None:
        """Write a command that has no final line (`reset`)."""
        self._validate(line)
        self.history.append(f"> {line}")
        self._serial.write((line + self.dialect.line_terminator).encode("ascii"))

    def pump(self, wait: float = 0.0) -> int:
        """Read whatever is available (waiting up to `wait` seconds for data) and dispatch complete lines."""
        deadline = time.monotonic() + wait
        count = 0
        while True:
            available = getattr(self._serial, "in_waiting", 0)
            data = self._serial.read(available or 1)
            for line in self._assembler.feed(data):
                self._dispatch(line)
                count += 1
            if (not data or count) and time.monotonic() >= deadline:
                return count

    def _dispatch(self, line: str) -> None:
        self.history.append(line)
        if self.dialect.is_event_line(line):
            event = self.dialect.parse_event(line)
            log.debug("event %s", event.raw)
            self._events.append(event)
        elif self.dialect.is_final_line(line):
            response = self.dialect.parse_response(line)
            log.debug("< %s", response.raw)
            if self._pending is None:
                log.warning("final line without pending command: %r", line)
            else:
                self._finals.append(response)
        else:
            self.other_lines.append(line)

    def events(self, kind: str | None = None) -> list[Event]:
        """Queued events (not removed)."""
        self.pump()
        return [event for event in self._events if kind is None or event.kind == kind]

    def drain_events(self, kind: str | None = None) -> list[Event]:
        """Remove and return queued events of `kind` (all when None)."""
        self.pump()
        taken = [event for event in self._events if kind is None or event.kind == kind]
        kept = [event for event in self._events if not (kind is None or event.kind == kind)]
        self._events.clear()
        self._events.extend(kept)
        return taken

    def wait_event(
        self,
        kind: str,
        predicate: Callable[[Event], bool] | None = None,
        timeout: float | None = None,
    ) -> Event:
        """Remove and return the oldest queued (or next arriving) event of `kind` accepted by `predicate`."""
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            for event in self._events:
                if event.kind == kind and (predicate is None or predicate(event)):
                    self._events.remove(event)
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TerminalTimeout(f"EVT {kind}", limit)
            self.pump(min(remaining, 0.02))

    def collect_events(self, kind: str, duration: float) -> list[Event]:
        """Listen for `duration` seconds and return (and remove) all `kind` events queued meanwhile."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.pump(min(0.02, max(0.0, deadline - time.monotonic())))
        return self.drain_events(kind)

    def wait_boot(self, timeout: float = 5.0, kind: str = "boot") -> Event:
        """Wait for the event the device prints after a reset (`EVT boot ...` by default)."""
        return self.wait_event(kind, timeout=timeout)

    def sync(self, attempts: int = 5, timeout: float = 0.5, command: str = "ping") -> None:
        """Clear a partially typed line on the device side (Ctrl-C) and wait until `command` answers `OK`."""
        for attempt in range(attempts):
            self._serial.write(CTRL_C)
            self.pump(0.05)
            self._pending = None
            self._finals.clear()
            try:
                self.command(command, timeout=timeout)
                return
            except (TerminalTimeout, FirmwareError):
                log.debug("sync attempt %d failed", attempt + 1)
        raise TerminalTimeout(command, timeout * attempts)

    def _validate(self, line: str) -> None:
        if not line or any(char < " " or char > "~" for char in line):
            raise ValueError(f"command must be printable ASCII: {line!r}")
        if len(line) > self.max_command_length:
            raise ValueError(f"command longer than {self.max_command_length} characters: {line[:40]}...")
