"""Offline double of the WaveForms library: `FakeDwfApi` behind the real `AnalogDiscovery3` wrapper."""

from __future__ import annotations

import ctypes
from collections import deque
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from .ad3 import AnalogDiscovery3
from .dwf import _FALLBACK_CONSTANTS


def _target(arg: Any) -> Any:
    return getattr(arg, "_obj", arg)


def _value(arg: Any) -> Any:
    return getattr(_target(arg), "value", arg)


class FakeDwfApi:
    """Records every `FDwf*` call and answers the out-parameters the wrapper reads.

    DigitalIO loops outputs back to inputs; `inputs` supplies levels of undriven DIOs. `logic_samples`,
    `uart_rx`, `can_rx` and `scope_levels` feed the corresponding acquisitions.
    """

    def __init__(self) -> None:
        self.constants = SimpleNamespace(**_FALLBACK_CONSTANTS)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.clock_hz = 100e6
        self.buffer_size = 32768
        self.pattern_buffer = 32768
        self.counter_max = 0x7FFF
        self.inputs = 0
        self.output_enable = 0
        self.outputs = 0
        self.logic_samples: list[int] = []
        self.uart_rx = bytearray()
        self.uart_tx = bytearray()
        self.can_rx: deque[tuple[int, bool, bytes]] = deque()
        self.can_tx: list[tuple[int, bool, bytes]] = []
        self.scope_levels = {0: 0.0, 1: 0.0}
        self.serial = "SN:210415ABCDEF"

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def calls_to(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args in self.calls if called == name]

    def last_error(self) -> str:
        return ""

    def version(self) -> str:
        return "fake"

    def __getattr__(self, name: str) -> Callable[..., int]:
        if not name.startswith("FDwf"):
            raise AttributeError(name)

        def call(*args: Any) -> int:
            self.calls.append((name, tuple(_value(arg) if not isinstance(arg, ctypes.Array) else arg for arg in args)))
            handler = getattr(self, "_" + name, None)
            if handler is not None:
                handler(*args)
            return 1

        return call

    @staticmethod
    def _set(arg: Any, value: Any) -> None:
        _target(arg).value = value

    def _FDwfEnum(self, _filter: Any, count: Any) -> None:
        self._set(count, 1)

    def _FDwfEnumDeviceName(self, _index: Any, buffer: Any) -> None:
        buffer.value = b"Analog Discovery 3"

    def _FDwfEnumSN(self, _index: Any, buffer: Any) -> None:
        buffer.value = self.serial.encode()

    def _FDwfDeviceOpen(self, _index: Any, handle: Any) -> None:
        self._set(handle, 1)

    def _FDwfDigitalOutInternalClockInfo(self, _h: Any, value: Any) -> None:
        self._set(value, self.clock_hz)

    _FDwfDigitalInInternalClockInfo = _FDwfDigitalOutInternalClockInfo

    def _FDwfDigitalInBufferSizeInfo(self, _h: Any, value: Any) -> None:
        self._set(value, self.buffer_size)

    def _FDwfDigitalOutDataInfo(self, _h: Any, _dio: Any, value: Any) -> None:
        self._set(value, self.pattern_buffer)

    def _FDwfDigitalOutCounterInfo(self, _h: Any, _dio: Any, low: Any, high: Any) -> None:
        self._set(low, 0)
        self._set(high, self.counter_max)

    def _done(self, *args: Any) -> None:
        self._set(args[-1], 2)

    _FDwfDigitalOutStatus = _done
    _FDwfDigitalInStatus = _done
    _FDwfAnalogInStatus = _done
    _FDwfAnalogOutStatus = _done

    def _FDwfDigitalIOOutputEnableSet(self, _h: Any, mask: Any) -> None:
        self.output_enable = _value(mask)

    def _FDwfDigitalIOOutputSet(self, _h: Any, mask: Any) -> None:
        self.outputs = _value(mask)

    def _FDwfDigitalIOInputStatus(self, _h: Any, value: Any) -> None:
        self._set(value, (self.outputs & self.output_enable) | (self.inputs & ~self.output_enable & 0xFFFF))

    def _FDwfDigitalInStatusData(self, _h: Any, buffer: Any, _size: Any) -> None:
        target = _target(buffer)
        for i in range(min(len(target), len(self.logic_samples))):
            target[i] = self.logic_samples[i]

    def _FDwfAnalogInStatusData(self, _h: Any, index: Any, buffer: Any, _size: Any) -> None:
        target = _target(buffer)
        for i in range(len(target)):
            target[i] = self.scope_levels[_value(index)]

    def _FDwfDigitalUartTx(self, _h: Any, buffer: Any, size: Any) -> None:
        if buffer is not None:
            self.uart_tx += ctypes.string_at(buffer, _value(size))

    def _FDwfDigitalUartRx(self, _h: Any, buffer: Any, size: Any, count: Any, parity: Any) -> None:
        taken = bytes(self.uart_rx[: _value(size)]) if buffer is not None else b""
        del self.uart_rx[: len(taken)]
        if taken:
            ctypes.memmove(buffer, taken, len(taken))
        self._set(count, len(taken))
        self._set(parity, 0)

    def _FDwfDigitalCanTx(self, _h: Any, ident: Any, ext: Any, _remote: Any, dlc: Any, buffer: Any) -> None:
        if _value(ident) >= 0 and buffer is not None:
            self.can_tx.append((_value(ident), bool(_value(ext)), bytes(buffer[: _value(dlc)])))

    def _FDwfDigitalCanRx(self, _h: Any, ident: Any, ext: Any, remote: Any, dlc: Any, buffer: Any, size: Any, status: Any) -> None:
        if _value(size) and self.can_rx:
            frame_id, frame_ext, data = self.can_rx.popleft()
            for i, byte in enumerate(data):
                buffer[i] = byte
            self._set(ident, frame_id)
            self._set(ext, int(frame_ext))
            self._set(remote, 0)
            self._set(dlc, len(data))
            self._set(status, 1)
        else:
            self._set(status, 0)


def fake_ad3(api: FakeDwfApi | None = None, analog_limits: tuple[float, float] = (0.0, 3.3)) -> AnalogDiscovery3:
    """An opened `AnalogDiscovery3` running on `FakeDwfApi`."""
    backend = api or FakeDwfApi()
    device = AnalogDiscovery3(analog_limits=analog_limits, api_factory=lambda: backend)
    device.open()
    return device
