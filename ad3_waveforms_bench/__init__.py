"""Drive a Digilent Analog Discovery 3 through the WaveForms SDK, and a device under test over a serial
command terminal, for hardware-in-the-loop test benches."""

from .instruments.ad3 import AnalogDiscovery3, InstrumentError, LogicCapture
from .protocol import Dialect, Event, ProtocolError, Response, format_command
from .terminal import FirmwareError, FirmwareTerminal, TerminalError, TerminalTimeout

__version__ = "0.1.0"

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
