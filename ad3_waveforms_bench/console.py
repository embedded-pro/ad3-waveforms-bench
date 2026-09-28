"""Interactive passthrough to a device command terminal, with history.

    ad3-bench-console --port /dev/ttyACM0
    ad3-bench-console --port /dev/ttyUSB0 --baud 115200 -c ping -c info

Lines are sent as typed; `OK`/`ERR` answers and queued `EVT` lines are printed. Meta commands:
`:wait <s>` listens for events, `:events` prints queued events, `:raw` toggles printing other output, `:quit`.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import sys
from pathlib import Path

from .terminal import DEFAULT_BAUD, FirmwareTerminal, TerminalError

HISTORY = Path.home() / ".ad3_bench_console_history"


def _setup_history(path: Path) -> None:
    try:
        import readline
    except ImportError:
        return
    with contextlib.suppress(OSError):
        readline.read_history_file(path)
    readline.set_history_length(1000)
    atexit.register(readline.write_history_file, path)


class Console:
    def __init__(self, terminal: FirmwareTerminal, timeout: float, raw: bool) -> None:
        self.terminal = terminal
        self.timeout = timeout
        self.raw = raw
        self._printed_other = len(terminal.other_lines)

    def flush(self) -> None:
        for event in self.terminal.drain_events():
            print(event.raw)
        other = list(self.terminal.other_lines)
        if self.raw:
            for line in other[self._printed_other :]:
                print(f"  | {line}")
        self._printed_other = len(other)

    def run(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return True
        if line in (":quit", ":q", ":exit"):
            return False
        if line == ":raw":
            self.raw = not self.raw
        elif line == ":events":
            self.terminal.pump()
        elif line.startswith(":wait"):
            seconds = float(line.split()[1]) if len(line.split()) > 1 else 1.0
            self.terminal.pump(seconds)
        elif line == "reset":
            self.terminal.drain_events("boot")
            self.terminal.send_nowait("reset")
            try:
                print(self.terminal.wait_boot(self.timeout).raw)
            except TerminalError as error:
                print(f"! {error}")
        else:
            try:
                print(self.terminal.command(line, timeout=self.timeout, check=False).raw)
            except (TerminalError, ValueError) as error:
                print(f"! {error}")
        self.flush()
        return True


def main(
    argv: list[str] | None = None,
    *,
    prog: str = "ad3-bench-console",
    name: str = "ad3-waveforms-bench console",
    prompt: str = "bench> ",
    baud: int = DEFAULT_BAUD,
    history: Path = HISTORY,
) -> int:
    """Run the console; the keyword arguments let a project wrap it with its own defaults."""
    parser = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="serial port of the device terminal")
    parser.add_argument("--baud", type=int, default=baud, help=f"baud rate (default {baud})")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for a final line")
    parser.add_argument("--raw", action="store_true", help="also print non-protocol output (traces)")
    parser.add_argument("--history", type=Path, default=history)
    parser.add_argument("-c", "--command", action="append", default=[], help="run command(s) and exit")
    args = parser.parse_args(argv)

    with FirmwareTerminal(args.port, args.baud, timeout=args.timeout) as terminal:
        try:
            terminal.sync()
        except TerminalError as error:
            print(f"! device not answering: {error}", file=sys.stderr)
            if not args.command:
                print("! continuing anyway", file=sys.stderr)
            else:
                return 1
        console = Console(terminal, args.timeout, args.raw)
        console.flush()
        if args.command:
            for command in args.command:
                console.run(command)
            return 0
        _setup_history(args.history)
        print(f"{name}, :quit to leave, :wait <s> to listen for events")
        while True:
            try:
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not console.run(line):
                return 0


if __name__ == "__main__":
    sys.exit(main())
