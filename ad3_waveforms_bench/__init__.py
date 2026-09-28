"""Drive a Digilent Analog Discovery 3 through the WaveForms SDK, and a device under test over a serial
command terminal, for hardware-in-the-loop test benches."""

from importlib.metadata import PackageNotFoundError, version

from .instruments.ad3 import AnalogDiscovery3, InstrumentError, LogicCapture
from .protocol import Dialect, Event, ProtocolError, Response, format_command
from .terminal import FirmwareError, FirmwareTerminal, TerminalError, TerminalTimeout

try:
    __version__ = version("ad3-waveforms-bench")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.2.0"

__all__ = [
    "AnalogDiscovery3",
    "Dialect",
    "Event",
    "FirmwareError",
    "FirmwareTerminal",
    "InstrumentError",
    "LogicCapture",
    "ProtocolError",
    "Response",
    "TerminalError",
    "TerminalTimeout",
    "format_command",
]
