"""Instrument wrappers. `ad3` imports the WaveForms SDK lazily (only when a device is opened)."""

from .ad3 import AnalogDiscovery3, InstrumentError, LogicCapture

__all__ = ["AnalogDiscovery3", "InstrumentError", "LogicCapture"]
