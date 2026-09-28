# ad3-waveforms-bench

[![CI](https://github.com/embedded-pro/ad3-waveforms-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/embedded-pro/ad3-waveforms-bench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ad3-waveforms-bench)](https://pypi.org/project/ad3-waveforms-bench/)
[![Python](https://img.shields.io/pypi/pyversions/ad3-waveforms-bench)](https://pypi.org/project/ad3-waveforms-bench/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Python toolkit for driving a [Digilent Analog Discovery 3](https://digilent.com/reference/test-and-measurement/analog-discovery-3/start) (AD3) through the WaveForms SDK for hardware-in-the-loop (HIL) test benches.

It bundles what a bench needs besides the device under test:

- `AnalogDiscovery3` - supplies, static DIO, pattern generator, logic analyzer, wavegen, scope and the UART/SPI/CAN protocol engines, on a thin `ctypes` binding of the WaveForms SDK.
- `analysis` - pure signal analysis of captures: frequency, duty cycle, edges, dead time, phase, quadrature/SPI/UART decoding, statistics.
- `FirmwareTerminal` - a serial client for a line-based command terminal (`OK`/`ERR` final lines, asynchronous `EVT` lines) on the device under test, plus the `ad3-bench-console` REPL.
- A pytest plugin (`--ad3-serial`, `--ad3-remote`, `--no-ad3`, `--fake`, the `ad3` marker and fixture) and in-memory fakes of the WaveForms library and of a device terminal, so benches are unit tested without hardware.
- `ad3-bench-server` - shares the AD3 of one machine (for example a Windows PC) over TCP, so benches run in a Docker container or on another machine; `ad3-bench-gui` is the same server with a window and a tray icon, also shipped as a Windows installer and a Linux AppImage.

It was split out of the [hal-ti](https://github.com/embedded-pro/hal-ti) hardware-in-the-loop validation, which uses it to validate TM4C drivers.

## Install

```bash
pip install ad3-waveforms-bench
# or the development version
pip install git+https://github.com/embedded-pro/ad3-waveforms-bench
```

Python 3.10 or newer. The Python dependencies are `pyserial` and `pytest`; the WaveForms SDK is not a Python package but a native runtime that must be installed separately.

### WaveForms runtime

| OS      | Install                                                                                             | Library loaded                          |
|---------|-----------------------------------------------------------------------------------------------------|-----------------------------------------|
| Linux   | Digilent Adept 2 runtime, then the WaveForms `.deb`/`.rpm` from the Digilent website                | `libdwf.so`                             |
| Windows | WaveForms installer from the Digilent website (includes the Adept runtime and the SDK)              | `dwf.dll`                               |
| macOS   | WaveForms `.dmg` from the Digilent website; the SDK framework is installed to `/Library/Frameworks` | `/Library/Frameworks/dwf.framework/dwf` |

- Set `DWF_LIBRARY` to the full path of the library when it is installed elsewhere.
- The SDK constants come from the official `dwfconstants.py` when it is found in the SDK samples directory (or in `DWF_CONSTANTS_DIR`); otherwise an identical built-in copy is used.
- Without the runtime everything except opening a device works: the analysis, terminal and fakes are pure Python, and the `ad3` fixture skips its tests.
- A machine without the runtime (a container) can use the AD3 of another machine through `ad3-bench-server`, see [Remote AD3](#remote-ad3-windows-host-docker-bench).

## Analog Discovery 3

User-facing channel numbers are DIO 0-15, wavegen W1/W2 = `1`/`2` and scope channels `1`/`2`. `open()` starts with every output released and V+/V- off; `close()` (or leaving the `with` block) returns to that state.

```python
from ad3_waveforms_bench import AnalogDiscovery3

with AnalogDiscovery3(serial="210415ABCDEF", analog_limits=(0.0, 3.3)) as ad3:
    ad3.supplies.set(vplus=3.3)
    ad3.dio.drive(0, 1)
    level = ad3.dio.read(1)
    ad3.dio.release(0)
```

Without `serial` (or `index`) the first device is opened. Wavegen levels outside `analog_limits` raise `ValueError` so a wrong parameter cannot overdrive an input.

### Pattern generator

```python
ad3.pattern.pulses(2, count=10, frequency=1000)
ad3.pattern.wait_done()
ad3.pattern.clock(3, frequency=1e6, duty=0.25)
ad3.pattern.custom({4: [0, 1, 1, 0], 5: [1, 0, 0, 1]}, rate=1e6, run_samples=0)
ad3.pattern.quadrature(0, 1, frequency=1000, cycles=25, direction="rev", z=2, index_every=5)
ad3.pattern.stop()
```

The methods return the frequency or sample rate actually used after quantisation to the device clock.

### Logic capture and analysis

```python
from ad3_waveforms_bench import analysis

capture = ad3.logic.record(rate=10e6, samples=16384, trigger=(0, "rising"), pretrigger=0.1)
capture.frequency(0), capture.duty(0), capture.edge_count(0, "rising")
capture.dead_times(0, 1)
capture.phase(0, 1)
capture.spi(clk=2, mosi=3, miso=4, cs=5, mode=0)
capture.uart(6, baud=115200, parity="even")

pending = ad3.logic.arm(rate=1e6, samples=8192, trigger=(7, "falling"))
# ... start the stimulus; the analyzer is already armed ...
capture = pending.wait(timeout=2.0)

bits = capture.channel(0)
analysis.high_low_times(bits, capture.rate)
analysis.quadrature_decode(capture.channel(0), capture.channel(1))
```

`LogicCapture` holds 16-bit samples (bit n is DIOn); every helper is also available as a plain function in `ad3_waveforms_bench.analysis` for lists of 0/1 samples.

### Wavegen and scope

```python
ad3.wavegen.dc(1, 1.65)
ad3.wavegen.sine(2, low=0.5, high=2.5, frequency=1000)
ad3.wavegen.square(2, low=0.0, high=3.3, frequency=500, cycles=10)
ad3.wavegen.wait_done(2)

volts = ad3.scope.average(1, samples=1000)
stats = ad3.scope.stats(2, rate=1e6, samples=4096)
traces = ad3.scope.acquire([1, 2], rate=1e5, samples=2000)
```

Connect the `-` input of each used scope channel to ground for single-ended measurements.

### UART, SPI and CAN

```python
ad3.uart.configure(tx=1, rx=0, baud=115200, parity="none", stop=1)
ad3.uart.write(b"hello")
reply = ad3.uart.read(5, timeout=0.5)

ad3.spi.configure(clk=0, mosi=2, miso=3, cs=1, frequency=1e6, mode=0)
rx = ad3.spi.transfer(b"\x9f\x00\x00\x00")

ad3.can.configure(tx=0, rx=1, bitrate=500_000)
ad3.can.send(0x123, b"\x01\x02")
frame = ad3.can.receive(timeout=1.0)
```

The CAN engine works at logic level. Without transceivers, build a wired-AND bus: join the controller RX pin, the AD3 RX DIO and a 1 kOhm pull-up to 3.3 V, and let each TX pin (controller and AD3) pull that node low through a Schottky diode with its anode on the bus.

## Terminal client

`FirmwareTerminal` talks to a command terminal on the device under test over a serial port (for example an EMIL `services::TerminalWithCommandsImpl`):

- The host writes one command per line, `name [positional...] [key=value...]`, terminated by `\r`.
- The device may echo characters and print a prompt (`>` and a space); echo, prompts, bells, backspaces and ANSI escape sequences are stripped.
- Every command ends with exactly one final line: `OK [key=value...]`, `ERR <reason>`, or a line the terminal prints for unknown commands (`Unrecognized command.`, reported as reason `unrecognized`).
- `EVT <kind> [key=value...]` lines are asynchronous and may appear at any time, even between a command and its final line; they are queued.
- Numbers are decimal or `0x` hexadecimal, binary payloads are hex strings (`a55a01`, `-` for empty) and lists are comma separated.

```python
from ad3_waveforms_bench import FirmwareError, FirmwareTerminal, format_command

with FirmwareTerminal("/dev/ttyACM0", baud=115200, timeout=2.0) as terminal:
    terminal.sync()
    info = terminal.command("info")
    board, clock = info["board"], info.as_int("sysclk")
    terminal.command(format_command("pwm.open", 0, gens=[0, 1], freq=20000, inva=True))
    try:
        terminal.command("can.open 0")
    except FirmwareError as error:
        print(error.reason)
    event = terminal.wait_event("can", lambda event: event.as_int("id") == 0x123, timeout=1.0)
    payload = event.as_bytes("data")
    terminal.send_nowait("reset")
    terminal.wait_boot(timeout=5.0)
```

- `command()` raises `FirmwareError(reason, command)` on `ERR` (`check=False` returns the `Response` instead) and `TerminalTimeout` without a final line.
- `begin()` writes a command without waiting and returns a pending command whose `wait()` collects the final line later.
- `wait_event()`, `events()`, `drain_events()` and `collect_events()` read the event queue; `sync()` clears a half-typed line with Ctrl-C and waits until `ping` (or another `command`) answers.
- `Response` and `Event` expose `args`, `values`, `as_int`, `as_float`, `as_bool`, `as_bytes`, `as_list` and `as_ints`.

The baud rate defaults to 115200; the prompt, the `OK`/`ERR`/`EVT` tokens, the unknown-command lines and the line terminator are set with a `Dialect`:

```python
from ad3_waveforms_bench import Dialect

dialect = Dialect(ok="+OK", err="-ERR", event="!", prompt="$ ", unrecognized=("?",), line_terminator="\n")
terminal = FirmwareTerminal("/dev/ttyUSB0", baud=115200, dialect=dialect)
```

### Console

```bash
ad3-bench-console --port /dev/ttyACM0 --baud 921600
ad3-bench-console --port /dev/ttyUSB0 --baud 115200 -c ping -c info
```

The console sends lines as typed, prints final lines and events, and keeps a history in `~/.ad3_bench_console_history`. `:wait <s>` listens for events, `:events` prints queued events, `:raw` also shows non-protocol output and `:quit` leaves. A project can wrap `ad3_waveforms_bench.console.main(argv, prog=..., baud=..., history=...)` to change the defaults.

## pytest plugin

Installing the package registers a pytest plugin (entry point `pytest11`) that adds:

| Option                     | Effect                                                                              |
|----------------------------|-------------------------------------------------------------------------------------|
| `--ad3-serial SERIAL`      | open the AD3 with this serial number (default: `AD3_SERIAL`, else the first device) |
| `--ad3-remote HOST[:PORT]` | use the AD3 of an `ad3-bench-server` (default: `AD3_REMOTE`)                        |
| `--no-ad3`                 | skip every test marked `ad3` or using the `ad3` fixture                             |
| `--fake`                   | back the `ad3` fixture with `FakeDwfApi` instead of the WaveForms runtime           |

- The `ad3` marker is registered, so it works with `--strict-markers`.
- The session fixture `ad3` opens the device once, skips the requesting tests when no device (or runtime) is available, and closes it at the end.
- Override the `ad3_settings` fixture in a `conftest.py` to change the analog limits or switch the supplies on.
- Projects read `--fake` themselves to swap their own device fakes in, for example a `FakeTerminalDevice` behind their terminal fixture.

```python
# conftest.py
import pytest

from ad3_waveforms_bench.pytest_plugin import Ad3Settings


@pytest.fixture(scope="session")
def ad3_settings():
    return Ad3Settings(analog_limits=(0.0, 3.3), vplus=3.3)
```

```python
# test_blink.py
import pytest

pytestmark = pytest.mark.ad3


def test_led_blinks(ad3):
    capture = ad3.logic.record_for(0.5, rate=1e5)
    assert capture.frequency(0) == pytest.approx(2.0, rel=0.05)
```

```bash
pytest --ad3-serial 210415ABCDEF
pytest --no-ad3
```

## Offline testing with fakes

- `ad3_waveforms_bench.instruments.fake.FakeDwfApi` stands in for the WaveForms library behind the real `AnalogDiscovery3`. It records every `FDwf*` call (`calls_to(name)`), loops DIO outputs back to inputs and feeds captures from `logic_samples`, `uart_rx`, `can_rx` and `scope_levels`. `fake_ad3()` returns an opened device on it.
- `ad3_waveforms_bench.fake_terminal.FakeTerminalDevice` behaves like a device terminal (echo, prompt, Ctrl-C, backspace, events printed before the next final line, optional noise) with a `handlers` table of commands; `FakeSerial` connects it to `FirmwareTerminal` and can return the output in random small chunks. Subclass it to emulate a particular firmware.

```python
from ad3_waveforms_bench.fake_terminal import FakeSerial, FakeTerminalDevice
from ad3_waveforms_bench.instruments.fake import FakeDwfApi, fake_ad3
from ad3_waveforms_bench.terminal import FirmwareTerminal

api = FakeDwfApi()
ad3 = fake_ad3(api)
ad3.pattern.pulses(2, count=10, frequency=1000)
assert api.calls_to("FDwfDigitalOutConfigure")

device = FakeTerminalDevice(style="line")
device.handlers["get"] = lambda device, args, options: "OK value=1"
terminal = FirmwareTerminal(serial=FakeSerial(device, chunk=3), timeout=0.5)
assert terminal.command("get led").as_int("value") == 1
```

## Remote AD3 (Windows host, Docker bench)

The AD3 is plugged into one machine (typically Windows with the WaveForms runtime), the benches run somewhere else (a Docker container, a Linux VM, a CI runner). `ad3-bench-server` runs next to the hardware and shares the WaveForms library over TCP. The serial port of the device under test is not this package's job: forward it with a byte-stream bridge such as [port-bridge](https://github.com/gabrielfrasantos/port-bridge) and open it as a pyserial `socket://` URL.

```text
 Docker container                                 Windows host
 ┌──────────────────────────────┐   TCP 5025     ┌──────────────────────────────┐
 │ pytest / AnalogDiscovery3    │ ─────────────▶ │ ad3-bench-server             │── dwf.dll ── AD3 (USB)
 │   RemoteDwfApi (AD3_REMOTE)  │   FDwf* calls  │                              │
 │ FirmwareTerminal             │   TCP 5000     │ port-bridge                  │── COM5 ───── DUT
 │   socket://host...:5000      │ ─────────────▶ │                              │
 └──────────────────────────────┘                └──────────────────────────────┘
```

On the Windows host, either install `ad3-bench-server-<version>-windows-setup.exe` from the [releases](https://github.com/embedded-pro/ad3-waveforms-bench/releases) (see [GUI](#gui)), or from Python:

```powershell
pip install ad3-waveforms-bench port-bridge
ad3-bench-server
port-bridge --serial-port COM5 --serial-baudrate 115200
```

In the container (Docker Desktop resolves `host.docker.internal` to the host):

```bash
export AD3_REMOTE=host.docker.internal:5025
pytest --ad3-remote host.docker.internal:5025      # or rely on AD3_REMOTE
ad3-bench-console --port socket://host.docker.internal:5000
```

```python
from ad3_waveforms_bench import AnalogDiscovery3, FirmwareTerminal

with AnalogDiscovery3(remote="host.docker.internal:5025") as ad3, FirmwareTerminal("socket://host.docker.internal:5000") as dut:
    ad3.dio.drive(0, 1)
    dut.command("ping")
```

- Every `FDwf*` call is forwarded with its ctypes arguments and out-parameters are written back (`RemoteDwfApi` replaces `DwfApi`), so the whole `AnalogDiscovery3` wrapper runs unchanged in the container and new SDK functions need no server update. The constants come from the server's runtime.
- Each call is one round trip (well under a millisecond on the same host). Acquisitions are single-shot and triggered on the device, so timing is not affected; only polling loops (UART/CAN receive) see the extra latency.
- One client at a time owns the AD3; others are refused with `busy`. When a client disconnects (or crashes), every device it left open is returned to the safe state (outputs released, V+/V- off) and closed.
- `AnalogDiscovery3(remote=...)`, `AD3_REMOTE` (and `AD3_REMOTE_TOKEN`) or `--ad3-remote` select the server; an explicit `api_factory` still wins.
- `FirmwareTerminal` and `ad3-bench-console` accept any pyserial URL as the port. Over `socket://` the baud rate is the one the bridge opened the port with, not the `baud` argument.
- `ad3-bench-server --fake` serves `FakeDwfApi`, to try the setup without hardware.
- The server listens on `127.0.0.1` by default. If the container cannot reach it (for example Docker Engine inside WSL2 rather than Docker Desktop), use `--host 0.0.0.0 --token <secret>`, set `AD3_REMOTE_TOKEN` in the container and keep the port behind the Windows firewall: whoever reaches the port controls the AD3.

[`examples/docker`](examples/docker) has a `Dockerfile`, a `compose.yaml` and an example test.

### GUI

`ad3-bench-gui` runs the same server behind a window, with the layout and behaviour of the [port-bridge](https://github.com/gabrielfrasantos/port-bridge) GUI:

- WaveForms library (default location, a custom path or the fake device) with *Detect devices*, bind address, TCP port and token, log level.
- Status dots for the server and the connected client, a Start/Stop button and a live log panel; the log is also written to a rotating file (`%APPDATA%\ad3-bench-server\` on Windows, `~/.cache/ad3-bench-server/` on Linux).
- A tray icon (show/hide, start/stop, check for updates, open the log); closing the window keeps the server running in the tray. Settings are remembered.
- `--start` starts the server right away (as does *Start the server on launch*) and `--minimized` starts in the tray; the Windows installer's optional autostart entry uses both.
- It checks the GitHub releases for a newer version at startup.

Every release attaches `ad3-bench-server-<version>-windows-setup.exe` (Inno Setup, no admin rights needed) and `ad3-bench-server-<version>-x86_64.AppImage`. From Python:

```bash
pip install "ad3-waveforms-bench[gui]"
ad3-bench-gui
```

## Why ctypes instead of pydwf

- `instruments/dwf.py` calls the `FDwf*` C functions through `ctypes`, exactly like the official WaveForms SDK Python samples, and uses the official `dwfconstants.py` values.
- This follows the SDK reference manual one to one, always matches the installed runtime (new devices and functions such as the AD3 protocol engines need no binding update) and adds no third-party dependency.
- `pydwf` is a well-made wrapper, but it pins the API to its generated signatures and adds a translation layer to debug against the Digilent documentation.
- All SDK calls are isolated in `instruments/dwf.py` and `instruments/ad3.py`, so `FakeDwfApi` can replace the library and the wrapper is unit tested offline.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,gui]"
ruff check .
ruff format --check .
mypy
pytest -q
```

CI runs these on Linux, Windows and macOS with Python 3.10-3.14 and checks the built wheel. Releases are made by release-please from [Conventional Commits](https://www.conventionalcommits.org/) and published to PyPI, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, see [LICENSE](LICENSE).
