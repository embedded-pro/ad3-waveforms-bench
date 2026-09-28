import pytest

from ad3_waveforms_bench.fake_terminal import FakeSerial, FakeTerminalDevice
from ad3_waveforms_bench.protocol import Dialect
from ad3_waveforms_bench.terminal import (
    FirmwareError,
    FirmwareTerminal,
    LineAssembler,
    TerminalTimeout,
    clean_line,
)


class Device(FakeTerminalDevice):
    """A small command set: `get <name>`, `set <name> <value>`, `open <index>`, `info`."""

    def __post_init__(self):
        self.values = {}
        self.opened = set()
        self.boots = 0
        super().__post_init__()

    def boot_message(self):
        self.boots += 1
        return f"EVT boot count={self.boots}"

    def lookup(self, name):
        return super().lookup(name) or getattr(self, "_cmd_" + name, None)

    @staticmethod
    def _cmd_get(device, args, options):
        return f"OK value={device.values.get(args[0], 0)}"

    @staticmethod
    def _cmd_set(device, args, options):
        device.values[args[0]] = int(args[1])
        return "OK"

    @staticmethod
    def _cmd_open(device, args, options):
        if args[0] in device.opened:
            return "ERR busy"
        device.opened.add(args[0])
        return "OK"

    @staticmethod
    def _cmd_info(device, args, options):
        return "OK name=fake speed=80000000"


def make_terminal(style="line", chunk=0, noise=False, **kwargs):
    device = Device(style=style, noise=noise)
    serial = FakeSerial(device, chunk=chunk)
    return FirmwareTerminal(serial=serial, timeout=0.5, **kwargs), device


def _prompt_only(device, args, options):
    device._write(device.prompt)
    return None


def test_clean_line_strips_prompt_escape_and_backspace():
    assert clean_line("> > pingx\b") == "ping"
    assert clean_line("\x1b[2K\x1b[1;5HOK\a value=1\x1b[K") == "OK value=1"
    assert clean_line(">") == ""
    assert clean_line("$ $ OK", prompt="$ ") == "OK"
    assert clean_line("> OK", prompt="") == "> OK"


def test_assembler_final_followed_by_prompt():
    assembler = LineAssembler()
    assert assembler.feed(b"> ping\r\n") == ["ping"]
    assert assembler.feed(b"\r\nOK") == []
    assert assembler.feed(b"> ") == ["OK"]


def test_assembler_releases_unterminated_event_after_idle():
    assembler = LineAssembler(idle_flush=0.05)
    assert assembler.feed(b"\r\nEVT boot board=x", now=0.0) == []
    assert assembler.poll(now=0.01) == []
    assert assembler.poll(now=0.1) == ["EVT boot board=x"]


def test_assembler_keeps_partial_escape_sequence():
    assembler = LineAssembler(idle_flush=0.0)
    assert assembler.feed(b"OK value=1\x1b[", now=0.0) == []
    assert assembler.feed(b"K\r\n", now=1.0) == ["OK value=1"]


@pytest.mark.parametrize("style", ["line", "trace"])
@pytest.mark.parametrize("chunk", [0, 1, 3, 7])
@pytest.mark.parametrize("noise", [False, True])
def test_command_framing(style, chunk, noise):
    terminal, device = make_terminal(style, chunk, noise)
    assert terminal.wait_boot(1.0).as_int("count") == 1
    assert terminal.command("ping").ok
    device.values["led"] = 1
    assert terminal.command("get led").as_int("value") == 1
    assert terminal.command("set led 0").ok
    assert device.values["led"] == 0
    info = terminal.command("info")
    assert info["name"] == "fake"
    assert info.as_int("speed") == 80_000_000


@pytest.mark.parametrize("style", ["line", "trace"])
def test_events_between_command_and_final_line(style):
    terminal, device = make_terminal(style, chunk=5)
    device.emit("EVT gpio pin=led count=3")
    device.emit("EVT wdt index=0 warning=1")
    assert terminal.command("ping").ok
    event = terminal.wait_event("wdt", timeout=0.1)
    assert event.as_int("warning") == 1
    assert [event.raw for event in terminal.drain_events("gpio")] == ["EVT gpio pin=led count=3"]


def test_wait_event_predicate_and_collect():
    terminal, device = make_terminal()
    terminal.drain_events()
    device.emit("EVT tick n=1", now=True)
    device.emit("EVT tick n=2", now=True)
    assert terminal.wait_event("tick", lambda event: event.as_int("n") == 2, timeout=0.2).as_int("n") == 2
    assert [event.as_int("n") for event in terminal.events("tick")] == [1]
    with pytest.raises(TerminalTimeout):
        terminal.wait_event("tock", timeout=0.05)
    device.emit("EVT tick n=3", now=True)
    assert [event.as_int("n") for event in terminal.collect_events("tick", 0.05)] == [1, 3]


def test_asynchronous_final_after_prompt():
    terminal, device = make_terminal("line")
    device.handlers["delay"] = _prompt_only
    pending = terminal.begin("delay 10")
    terminal.pump()
    device.output += b"\r\nOK\r\n"
    assert pending.wait().ok


def test_error_raises_firmware_error():
    terminal, _ = make_terminal()
    terminal.command("open 0")
    with pytest.raises(FirmwareError) as error:
        terminal.command("open 0")
    assert error.value.reason == "busy"
    assert error.value.command == "open 0"
    assert terminal.command("open 0", check=False).reason == "busy"


def test_unrecognized_command():
    terminal, _ = make_terminal()
    with pytest.raises(FirmwareError) as error:
        terminal.command("nosuch.command")
    assert error.value.reason == "unrecognized"


def test_timeout_without_final_line():
    terminal, device = make_terminal()
    device.handlers["hang"] = _prompt_only
    with pytest.raises(TerminalTimeout):
        terminal.command("hang", timeout=0.1)
    assert terminal.command("ping").ok


def test_reset_and_boot_event():
    terminal, _ = make_terminal("trace")
    assert terminal.wait_boot(1.0).as_int("count") == 1
    terminal.send_nowait("reset")
    assert terminal.wait_boot(1.0).as_int("count") == 2


def test_sync_clears_partial_input():
    terminal, device = make_terminal()
    terminal.write_raw(b"set le")
    terminal.sync()
    assert device.received[-1] == "ping"


def test_sync_with_other_command():
    terminal, device = make_terminal()
    terminal.sync(command="info")
    assert device.received[-1] == "info"


def test_command_validation():
    terminal, _ = make_terminal(max_command_length=20)
    with pytest.raises(ValueError):
        terminal.command("x" * 21)
    with pytest.raises(ValueError):
        terminal.command("ping\r")


def test_context_manager_closes_serial():
    serial = FakeSerial()
    with FirmwareTerminal(serial=serial) as terminal:
        terminal.command("ping")
    assert serial.closed


@pytest.mark.parametrize("chunk", [0, 3])
def test_custom_dialect(chunk):
    dialect = Dialect(ok="+OK", err="-ERR", event="!", prompt="$ ", unrecognized=("?",), line_terminator="\n")
    device = FakeTerminalDevice(style="line", prompt="$ ", unrecognized="?", boot_line="! ready")
    device.handlers["ping"] = lambda device, args, options: "+OK pong=1"
    device.handlers["fail"] = lambda device, args, options: "-ERR range"
    original_feed = device.feed
    device.feed = lambda data: original_feed(data.replace(b"\n", b"\r"))
    serial = FakeSerial(device, chunk=chunk)
    terminal = FirmwareTerminal(serial=serial, timeout=0.5, dialect=dialect)
    assert terminal.wait_event("ready", timeout=1.0).kind == "ready"
    assert terminal.command("ping").as_int("pong") == 1
    assert serial.written.endswith(b"ping\n")
    with pytest.raises(FirmwareError) as error:
        terminal.command("fail")
    assert error.value.reason == "range"
    assert terminal.command("nosuch", check=False).reason == "unrecognized"
