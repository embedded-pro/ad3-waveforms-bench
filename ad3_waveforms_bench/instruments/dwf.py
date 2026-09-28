"""The only module that touches the Digilent WaveForms SDK (`dwf` shared library, via ctypes).

The constants are the official `dwfconstants.py` values shipped with the WaveForms SDK samples. When
that file is importable (the SDK samples directory is on `sys.path` or `DWF_CONSTANTS_DIR` points to it)
it is used as is; otherwise the identical values below are used.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from ctypes import c_byte, c_int, c_ubyte
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_FALLBACK_CONSTANTS: dict[str, Any] = {
    "hdwfNone": c_int(0),
    "enumfilterAll": c_int(0),
    "DwfStateReady": c_ubyte(0),
    "DwfStateArmed": c_ubyte(1),
    "DwfStateDone": c_ubyte(2),
    "DwfStateTriggered": c_ubyte(3),
    "DwfStateRunning": c_ubyte(3),
    "DwfStateConfig": c_ubyte(4),
    "DwfStatePrefill": c_ubyte(5),
    "DwfStateWait": c_ubyte(7),
    "trigsrcNone": c_ubyte(0),
    "trigsrcPC": c_ubyte(1),
    "trigsrcDetectorAnalogIn": c_ubyte(2),
    "trigsrcDetectorDigitalIn": c_ubyte(3),
    "trigsrcAnalogIn": c_ubyte(4),
    "trigsrcDigitalIn": c_ubyte(5),
    "trigsrcDigitalOut": c_ubyte(6),
    "trigsrcAnalogOut1": c_ubyte(7),
    "trigsrcAnalogOut2": c_ubyte(8),
    "acqmodeSingle": c_int(0),
    "acqmodeScanShift": c_int(1),
    "acqmodeScanScreen": c_int(2),
    "acqmodeRecord": c_int(3),
    "funcDC": c_ubyte(0),
    "funcSine": c_ubyte(1),
    "funcSquare": c_ubyte(2),
    "funcTriangle": c_ubyte(3),
    "funcRampUp": c_ubyte(4),
    "funcRampDown": c_ubyte(5),
    "AnalogOutNodeCarrier": c_int(0),
    "DwfAnalogOutIdleDisable": c_int(0),
    "DwfAnalogOutIdleOffset": c_int(1),
    "DwfAnalogOutIdleInitial": c_int(2),
    "filterDecimate": c_int(0),
    "filterAverage": c_int(1),
    "DwfDigitalOutOutputPushPull": c_int(0),
    "DwfDigitalOutOutputOpenDrain": c_int(1),
    "DwfDigitalOutTypePulse": c_int(0),
    "DwfDigitalOutTypeCustom": c_int(1),
    "DwfDigitalOutIdleInit": c_int(0),
    "DwfDigitalOutIdleLow": c_int(1),
    "DwfDigitalOutIdleHigh": c_int(2),
    "DwfDigitalOutIdleZet": c_int(3),
    "DwfTriggerSlopeRise": c_int(0),
    "DwfTriggerSlopeFall": c_int(1),
    "DwfTriggerSlopeEither": c_int(2),
    "DwfParamOnClose": c_int(4),
}

_SDK_SAMPLE_DIRS = (
    "/usr/share/digilent/waveforms/samples/py",
    "/Applications/WaveForms.app/Contents/Resources/SDK/samples/py",
    r"C:\Program Files\Digilent\WaveFormsSDK\samples\py",
    r"C:\Program Files (x86)\Digilent\WaveFormsSDK\samples\py",
)


class DwfError(RuntimeError):
    pass


def load_constants() -> SimpleNamespace:
    values = dict(_FALLBACK_CONSTANTS)
    extra = os.environ.get("DWF_CONSTANTS_DIR")
    search = [extra] if extra else []
    search += [directory for directory in _SDK_SAMPLE_DIRS if Path(directory).is_dir()]
    added = [directory for directory in search if directory not in sys.path]
    sys.path.extend(added)
    try:
        import dwfconstants  # type: ignore[import-not-found]

        values.update({name: getattr(dwfconstants, name) for name in values if hasattr(dwfconstants, name)})
    except ImportError:
        pass
    finally:
        for directory in added:
            sys.path.remove(directory)
    return SimpleNamespace(**values)


def load_library(path: str | None = None) -> ctypes.CDLL:
    """Load `dwf` like the official samples do; `DWF_LIBRARY` overrides the location."""
    path = path or os.environ.get("DWF_LIBRARY")
    if path:
        return ctypes.cdll.LoadLibrary(path)
    if sys.platform.startswith("win"):
        return ctypes.cdll.LoadLibrary("dwf.dll")
    if sys.platform.startswith("darwin"):
        return ctypes.cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
    return ctypes.cdll.LoadLibrary("libdwf.so")


class DwfApi:
    """Checked access to `FDwf*` functions: `api.FDwfDeviceOpen(c_int(-1), byref(handle))`.

    Every call returning 0 raises `DwfError` with `FDwfGetLastErrorMsg`.
    """

    def __init__(self, library: Any | None = None) -> None:
        self._lib = library if library is not None else load_library()
        self.constants = load_constants()

    def __getattr__(self, name: str) -> Callable[..., int]:
        if not name.startswith("FDwf"):
            raise AttributeError(name)
        function = getattr(self._lib, name)

        def checked(*args: Any) -> int:
            result = function(*args)
            if result == 0:
                raise DwfError(f"{name}: {self.last_error()}")
            return result

        return checked

    def last_error(self) -> str:
        buffer = ctypes.create_string_buffer(512)
        self._lib.FDwfGetLastErrorMsg(buffer)
        return buffer.value.decode(errors="replace").strip() or "unknown error"

    def version(self) -> str:
        buffer = ctypes.create_string_buffer(32)
        self._lib.FDwfGetVersion(buffer)
        return buffer.value.decode()


def byte_array(data: bytes) -> ctypes.Array[c_ubyte]:
    return (c_ubyte * max(1, len(data)))(*data)


def char_array(size: int) -> ctypes.Array[c_byte]:
    return (c_byte * max(1, size))()
