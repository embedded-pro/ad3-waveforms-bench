import pytest

from ad3_waveforms_bench.console import Console, main
from ad3_waveforms_bench.fake_terminal import FakeSerial, FakeTerminalDevice
from ad3_waveforms_bench.terminal import FirmwareTerminal


def make_console(raw=False):
    device = FakeTerminalDevice(style="line")
    terminal = FirmwareTerminal(serial=FakeSerial(device), timeout=0.5)
    return Console(terminal, timeout=0.5, raw=raw), device


def test_commands_events_and_quit(capsys):
    console, device = make_console()
    device.handlers["hello"] = lambda device, args, options: "OK name=" + args[0]
    console.flush()
    assert console.run("hello bench")
    assert console.run("nosuch")
    device.emit("EVT tick n=1", now=True)
    assert console.run(":wait 0.05")
    assert console.run("")
    assert not console.run(":quit")
    output = capsys.readouterr().out.splitlines()
    assert output == ["EVT boot", "OK name=bench", "Unrecognized command.", "EVT tick n=1"]


def test_reset_prints_boot_event(capsys):
    console, _ = make_console()
    console.flush()
    capsys.readouterr()
    console.run("reset")
    assert capsys.readouterr().out.splitlines() == ["EVT boot"]


def test_raw_shows_other_output(capsys):
    console, device = make_console()
    console.flush()
    console.run(":raw")
    device.emit("trace text", now=True)
    console.run(":events")
    assert "  | trace text" in capsys.readouterr().out


def test_help_shows_baud(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--baud" in help_text
    assert "ad3-bench-console" in help_text
