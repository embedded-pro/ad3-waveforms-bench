"""Offline double of a device terminal: `FakeTerminalDevice` behaves like an EMIL command terminal (echo,
prompt, Ctrl-C, backspace, `EVT` lines) and `FakeSerial` connects it to `FirmwareTerminal`.

Subclass `FakeTerminalDevice` (or fill `handlers`) to emulate the commands of a particular firmware.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from .protocol import UNRECOGNIZED_TEXT

Handler = Callable[["FakeTerminalDevice", list[str], dict[str, str]], "str | list[str] | None"]


def _ping(device: FakeTerminalDevice, args: list[str], options: dict[str, str]) -> str:
    return "OK"


def _reset(device: FakeTerminalDevice, args: list[str], options: dict[str, str]) -> None:
    device.boot()
    return None


@dataclass
class FakeTerminalDevice:
    """Emulates a device behind a line terminal.

    `style="trace"` prints each line as `\\r\\n<line>` (like EMIL's `Tracer::Trace`), `"line"` as `<line>\\r\\n`.
    `noise` sprinkles bells and ANSI erase sequences into the output. Lines queued with `emit()` are printed
    before the next final line (or immediately with `emit(..., now=True)`). A handler returns the final
    line(s), or None for a command without final line. `boot_line` is printed after the prompt on boot.
    """

    style: Literal["trace", "line"] = "trace"
    noise: bool = False
    prompt: str = "> "
    unrecognized: str = UNRECOGNIZED_TEXT
    boot_line: str | None = "EVT boot"
    output: bytearray = field(default_factory=bytearray)
    handlers: dict[str, Handler] = field(default_factory=dict)
    received: list[str] = field(default_factory=list)

    default_handlers: ClassVar[dict[str, Handler]] = {"ping": _ping, "reset": _reset}

    def __post_init__(self) -> None:
        self._line = ""
        self.command_name = ""
        self._pending_events: list[str] = []
        self.boot()

    def boot(self) -> None:
        self._line = ""
        self._write(self.prompt)
        line = self.boot_message()
        if line:
            self._print(line)

    def boot_message(self) -> str | None:
        return self.boot_line

    def emit(self, line: str, now: bool = False) -> None:
        if now:
            self._print(line)
        else:
            self._pending_events.append(line)

    def feed(self, data: bytes) -> None:
        for char in data.decode("latin-1"):
            if char == "\r":
                self._enter()
            elif char == "\n":
                continue
            elif char == "\x03":
                self._line = ""
                self._write("\r" + self.prompt)
            elif char in "\b\x7f":
                if self._line:
                    self._line = self._line[:-1]
                    self._write("\b \b")
            elif " " <= char <= "~":
                self._line += char
                self._write(char)
            else:
                self._write("\a")

    def lookup(self, name: str) -> Handler | None:
        return self.handlers.get(name) or self.default_handlers.get(name)

    def _enter(self) -> None:
        line, self._line = self._line, ""
        self._write("\r\n")
        if line:
            self.received.append(line)
            result = self._execute(line)
            for event in self._pending_events:
                self._print(event)
            self._pending_events.clear()
            if result is None:
                return
            for item in [result] if isinstance(result, str) else result:
                self._print(item)
        self._write(self.prompt)

    def _execute(self, line: str) -> str | list[str] | None:
        tokens = line.split()
        name, rest = tokens[0], tokens[1:]
        self.command_name = name
        args = [token for token in rest if "=" not in token]
        options = dict(token.split("=", 1) for token in rest if "=" in token)
        handler = self.lookup(name)
        if handler is None:
            return self.unrecognized
        try:
            return handler(self, args, options)
        except (IndexError, ValueError, KeyError):
            return "ERR usage"

    def _print(self, line: str) -> None:
        if self.noise:
            line = line.replace(" ", " \a", 1) + "\x1b[K"
        self._write(f"\r\n{line}" if self.style == "trace" else f"{line}\r\n")

    def _write(self, text: str) -> None:
        self.output += text.encode("latin-1")


class FakeSerial:
    """pyserial stand-in wired to a `FakeTerminalDevice`; `chunk` > 0 returns output in random small pieces."""

    def __init__(self, device: FakeTerminalDevice | None = None, chunk: int = 0, seed: int = 1) -> None:
        self.device = device if device is not None else FakeTerminalDevice()
        self.chunk = chunk
        self._random = random.Random(seed)
        self.written = bytearray()
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self.device.output)

    def write(self, data: bytes) -> int:
        self.written += data
        self.device.feed(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        output = self.device.output
        if self.chunk:
            size = min(size, self._random.randint(1, self.chunk))
        data = bytes(output[:size])
        del output[:size]
        return data

    def close(self) -> None:
        self.closed = True
