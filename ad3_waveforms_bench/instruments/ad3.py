"""Digilent Analog Discovery 3 instrument wrapper (WaveForms SDK over ctypes, see `dwf.py`).

User-facing channel numbers: DIO 0-15, wavegen W1/W2 = 1/2, scope channels 1/2.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from ctypes import byref, c_double, c_int, c_ubyte, c_uint, c_uint16, create_string_buffer
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .. import analysis

if TYPE_CHECKING:
    from .dwf import DwfApi

log = logging.getLogger(__name__)

Slope = Literal["rising", "falling", "either"]
Parity = Literal["none", "odd", "even"]

DIO_COUNT = 16


class InstrumentError(RuntimeError):
    pass


def _state_value(state: Any) -> int:
    return int(getattr(state, "value", state))


@dataclass(frozen=True)
class LogicCapture:
    """16-bit DIO samples; bit n is DIOn."""

    rate: float
    samples: Sequence[int]
    trigger_index: int | None = None

    def channel(self, dio: int) -> list[int]:
        mask = 1 << dio
        return [1 if word & mask else 0 for word in self.samples]

    def channels(self, *dios: int) -> list[list[int]]:
        return [self.channel(dio) for dio in dios]

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate

    def frequency(self, dio: int) -> float:
        return analysis.frequency(self.channel(dio), self.rate)

    def duty(self, dio: int) -> float:
        return analysis.duty_cycle(self.channel(dio))

    def edge_count(self, dio: int, kind: analysis.EdgeKind = "both") -> int:
        return analysis.edge_count(self.channel(dio), kind)

    def dead_times(self, a: int, b: int, active_high: bool = True) -> list[float]:
        return analysis.dead_times(self.channel(a), self.channel(b), self.rate, active_high)

    def phase(self, a: int, b: int) -> tuple[float, float]:
        return analysis.phase_between(self.channel(a), self.channel(b), self.rate)

    def spi(self, clk: int, mosi: int, miso: int | None = None, cs: int | None = None, mode: int = 0) -> list[analysis.SpiFrame]:
        return analysis.spi_decode(
            self.channel(clk),
            self.channel(mosi),
            None if miso is None else self.channel(miso),
            None if cs is None else self.channel(cs),
            mode,
        )

    def uart(self, dio: int, baud: float, parity: Parity = "none", stop_bits: int = 1) -> list[analysis.UartByte]:
        return analysis.uart_decode(self.channel(dio), self.rate, baud, parity=parity, stop_bits=stop_bits)

    def quadrature(self, a: int, b: int, z: int | None = None) -> analysis.QuadratureResult:
        return analysis.quadrature_decode(self.channel(a), self.channel(b), None if z is None else self.channel(z))


@dataclass
class CanRxFrame:
    id: int
    ext: bool
    remote: bool
    data: bytes


class _Instrument:
    def __init__(self, device: AnalogDiscovery3) -> None:
        self._device = device

    @property
    def _api(self) -> DwfApi:
        return self._device.api

    @property
    def _h(self) -> c_int:
        return self._device.handle

    @property
    def _c(self) -> Any:
        return self._device.api.constants


class Supplies(_Instrument):
    """V+/V- programmable supplies. Both stay off unless explicitly enabled."""

    def set(self, vplus: float | None = None, vminus: float | None = None) -> None:
        if vplus is not None and not 0.5 <= vplus <= 5.0:
            raise ValueError("V+ must be within 0.5..5 V")
        if vminus is not None and not -5.0 <= vminus <= -0.5:
            raise ValueError("V- must be within -5..-0.5 V")
        api, h = self._api, self._h
        for channel, value in ((0, vplus), (1, vminus)):
            api.FDwfAnalogIOChannelNodeSet(h, c_int(channel), c_int(0), c_double(0 if value is None else 1))
            if value is not None:
                api.FDwfAnalogIOChannelNodeSet(h, c_int(channel), c_int(1), c_double(value))
        api.FDwfAnalogIOEnableSet(h, c_int(int(vplus is not None or vminus is not None)))
        api.FDwfAnalogIOConfigure(h)

    def off(self) -> None:
        self.set(None, None)


class StaticIo(_Instrument):
    """Static DigitalIO: per-pin output enable and level, input read-back."""

    def __init__(self, device: AnalogDiscovery3) -> None:
        super().__init__(device)
        self._enable = 0
        self._output = 0

    def drive(self, dio: int, level: int | bool) -> None:
        self.drive_many({dio: level})

    def drive_many(self, levels: Mapping[int, int | bool]) -> None:
        for dio, level in levels.items():
            _check_dio(dio)
            self._enable |= 1 << dio
            self._output = (self._output | (1 << dio)) if level else (self._output & ~(1 << dio))
        self._apply()

    def release(self, *dios: int) -> None:
        for dio in dios:
            _check_dio(dio)
            self._enable &= ~(1 << dio)
        self._apply()

    def release_all(self) -> None:
        self._enable = 0
        self._output = 0
        self._apply()

    def read_all(self) -> int:
        value = c_uint()
        self._api.FDwfDigitalIOStatus(self._h)
        self._api.FDwfDigitalIOInputStatus(self._h, byref(value))
        return int(value.value)

    def read(self, dio: int) -> int:
        _check_dio(dio)
        return (self.read_all() >> dio) & 1

    def pull(self, up: Sequence[int] = (), down: Sequence[int] = ()) -> None:
        """Weak pulls where the device supports them (`FDwfDigitalIOPullSet`)."""
        self._api.FDwfDigitalIOPullSet(self._h, c_uint(_mask(up)), c_uint(_mask(down)))
        self._api.FDwfDigitalIOConfigure(self._h)

    def _apply(self) -> None:
        self._api.FDwfDigitalIOOutputEnableSet(self._h, c_uint(self._enable))
        self._api.FDwfDigitalIOOutputSet(self._h, c_uint(self._output))
        self._api.FDwfDigitalIOConfigure(self._h)


class PatternGenerator(_Instrument):
    """Digital out (pattern generator): pulses, clocks, custom sequences, quadrature encoder signals."""

    @property
    def clock_hz(self) -> float:
        value = c_double()
        self._api.FDwfDigitalOutInternalClockInfo(self._h, byref(value))
        return float(value.value)

    def _counter_max(self, dio: int) -> int:
        low, high = c_uint(), c_uint()
        try:
            self._api.FDwfDigitalOutCounterInfo(self._h, c_int(dio), byref(low), byref(high))
            return int(high.value) or 0x7FFF
        except Exception:  # noqa: BLE001 - older runtimes lack the call; the AD2/AD3 counters are at least 15 bit
            return 0x7FFF

    def _data_max(self, dio: int) -> int:
        value = c_uint()
        self._api.FDwfDigitalOutDataInfo(self._h, c_int(dio), byref(value))
        return int(value.value)

    def _begin(self) -> None:
        self._api.FDwfDigitalOutReset(self._h)

    def _idle(self, idle: str) -> Any:
        return {"low": self._c.DwfDigitalOutIdleLow, "high": self._c.DwfDigitalOutIdleHigh, "z": self._c.DwfDigitalOutIdleZet}[idle]

    def _setup_pulse(self, dio: int, frequency: float, duty: float, start_high: bool) -> float:
        if not 0 < duty < 1:
            raise ValueError("duty must be within (0, 1)")
        clock = self.clock_hz
        divider = max(1, math.ceil(clock / frequency / self._counter_max(dio)))
        period = max(2, round(clock / divider / frequency))
        high = min(period - 1, max(1, round(period * duty)))
        low = period - high
        api, h = self._api, self._h
        api.FDwfDigitalOutEnableSet(h, c_int(dio), c_int(1))
        api.FDwfDigitalOutTypeSet(h, c_int(dio), self._c.DwfDigitalOutTypePulse)
        api.FDwfDigitalOutDividerSet(h, c_int(dio), c_uint(divider))
        api.FDwfDigitalOutCounterSet(h, c_int(dio), c_uint(low), c_uint(high))
        api.FDwfDigitalOutCounterInitSet(h, c_int(dio), c_int(int(start_high)), c_uint(high if start_high else low))
        return clock / divider / period

    def pulses(
        self,
        dio: int,
        count: int,
        frequency: float,
        duty: float = 0.5,
        idle: Literal["low", "high", "z"] = "low",
        start: bool = True,
    ) -> float:
        """Exactly `count` pulses starting with the active level (high when idle is low); returns the frequency."""
        _check_dio(dio)
        self._begin()
        active_high = idle != "high"
        actual = self._setup_pulse(dio, frequency, duty if active_high else 1 - duty, start_high=active_high)
        self._api.FDwfDigitalOutIdleSet(self._h, c_int(dio), self._idle(idle))
        self._run(count / actual, start)
        return actual

    def clock(self, dio: int, frequency: float, duty: float = 0.5, start: bool = True) -> float:
        """Free-running clock until `stop()`."""
        _check_dio(dio)
        self._begin()
        actual = self._setup_pulse(dio, frequency, duty, start_high=True)
        self._api.FDwfDigitalOutIdleSet(self._h, c_int(dio), self._c.DwfDigitalOutIdleLow)
        self._run(0.0, start)
        return actual

    def custom(
        self,
        channels: Mapping[int, Sequence[int]],
        rate: float,
        run_samples: int | None = None,
        idle: Literal["low", "high", "z"] = "low",
        start: bool = True,
    ) -> float:
        """Play per-DIO bit sequences at `rate`; sequences loop when `run_samples` exceeds their length.

        `run_samples=0` loops forever. Returns the actual sample rate.
        """
        if not channels:
            raise ValueError("no channels")
        self._begin()
        clock = self.clock_hz
        divider = max(1, round(clock / rate))
        actual = clock / divider
        length = max(len(bits) for bits in channels.values())
        api, h = self._api, self._h
        for dio, bits in channels.items():
            _check_dio(dio)
            if len(bits) > self._data_max(dio):
                raise ValueError(f"DIO{dio}: {len(bits)} samples exceed the pattern buffer ({self._data_max(dio)})")
            packed = _pack_bits(bits)
            api.FDwfDigitalOutEnableSet(h, c_int(dio), c_int(1))
            api.FDwfDigitalOutTypeSet(h, c_int(dio), self._c.DwfDigitalOutTypeCustom)
            api.FDwfDigitalOutDividerSet(h, c_int(dio), c_uint(divider))
            api.FDwfDigitalOutIdleSet(h, c_int(dio), self._idle(idle))
            api.FDwfDigitalOutDataSet(h, c_int(dio), byref(packed), c_uint(len(bits)))
        samples = length if run_samples is None else run_samples
        self._run(samples / actual, start)
        return actual

    def quadrature(
        self,
        a: int,
        b: int,
        frequency: float,
        cycles: int,
        direction: Literal["fwd", "rev"] = "fwd",
        z: int | None = None,
        index_every: int | None = None,
        start: bool = True,
    ) -> float:
        """`cycles` full encoder cycles at `frequency` (A leads B for `fwd`); `cycles=0` runs until `stop()`.

        With `z`, an index pulse of a quarter cycle is emitted every `index_every` cycles (default: once per run
        pattern of one cycle, i.e. every cycle). Returns the actual encoder frequency.
        """
        period_cycles = index_every if (z is not None and index_every) else 1
        pattern = analysis.quadrature_pattern(period_cycles, direction, index_every=period_cycles if z is not None else None)
        channels: dict[int, Sequence[int]] = {a: pattern["a"], b: pattern["b"]}
        if z is not None:
            channels[z] = pattern["z"]
        rate = self.custom(channels, frequency * 4, run_samples=4 * cycles, start=start)
        return rate / 4

    def _run(self, seconds: float, start: bool) -> None:
        api, h = self._api, self._h
        api.FDwfDigitalOutRunSet(h, c_double(seconds))
        api.FDwfDigitalOutRepeatSet(h, c_uint(1 if seconds > 0 else 0))
        if start:
            api.FDwfDigitalOutConfigure(h, c_int(1))

    def start(self) -> None:
        self._api.FDwfDigitalOutConfigure(self._h, c_int(1))

    def done(self) -> bool:
        state = c_ubyte()
        self._api.FDwfDigitalOutStatus(self._h, byref(state))
        return _state_value(state) == _state_value(self._c.DwfStateDone)

    def wait_done(self, timeout: float = 5.0) -> None:
        _poll(self.done, timeout, "pattern generator run")

    def stop(self) -> None:
        self._api.FDwfDigitalOutConfigure(self._h, c_int(0))
        self._api.FDwfDigitalOutReset(self._h)
        self._api.FDwfDigitalOutConfigure(self._h, c_int(0))


@dataclass
class PendingCapture:
    analyzer: LogicAnalyzer
    rate: float
    samples: int
    trigger_index: int | None

    def wait(self, timeout: float = 5.0) -> LogicCapture:
        return self.analyzer._collect(self, timeout)


class LogicAnalyzer(_Instrument):
    """Digital in: single acquisitions of up to `buffer_size` 16-bit samples."""

    @property
    def clock_hz(self) -> float:
        value = c_double()
        self._api.FDwfDigitalInInternalClockInfo(self._h, byref(value))
        return float(value.value)

    @property
    def buffer_size(self) -> int:
        value = c_int()
        self._api.FDwfDigitalInBufferSizeInfo(self._h, byref(value))
        return int(value.value)

    def arm(
        self,
        rate: float,
        samples: int,
        trigger: tuple[int, Slope] | Literal["pattern"] | None = None,
        pretrigger: float = 0.1,
        arm_timeout: float = 2.0,
    ) -> PendingCapture:
        """Configure and start; returns once armed, so a stimulus sent afterwards cannot be missed."""
        buffer = self.buffer_size
        if samples > buffer:
            raise ValueError(f"{samples} samples exceed the logic analyzer buffer ({buffer})")
        api, h, c = self._api, self._h, self._c
        clock = self.clock_hz
        divider = max(1, round(clock / rate))
        api.FDwfDigitalInAcquisitionModeSet(h, c.acqmodeSingle)
        api.FDwfDigitalInDividerSet(h, c_uint(divider))
        api.FDwfDigitalInSampleFormatSet(h, c_int(16))
        api.FDwfDigitalInBufferSizeSet(h, c_int(samples))
        trigger_index: int | None = None
        if trigger is None:
            api.FDwfDigitalInTriggerSourceSet(h, c.trigsrcNone)
            api.FDwfDigitalInTriggerPositionSet(h, c_uint(samples))
        else:
            before = int(samples * pretrigger)
            trigger_index = before
            api.FDwfDigitalInTriggerPositionSet(h, c_uint(samples - before))
            if trigger == "pattern":
                api.FDwfDigitalInTriggerSourceSet(h, c.trigsrcDigitalOut)
            else:
                dio, slope = trigger
                _check_dio(dio)
                rise = 1 << dio if slope in ("rising", "either") else 0
                fall = 1 << dio if slope in ("falling", "either") else 0
                api.FDwfDigitalInTriggerSourceSet(h, c.trigsrcDetectorDigitalIn)
                api.FDwfDigitalInTriggerSet(h, c_uint(0), c_uint(0), c_uint(rise), c_uint(fall))
        api.FDwfDigitalInConfigure(h, c_int(1), c_int(1))
        pending = PendingCapture(self, clock / divider, samples, trigger_index)
        if trigger is not None:
            waiting = {_state_value(c.DwfStateConfig), _state_value(c.DwfStatePrefill)}
            _poll(lambda: self._status(False) not in waiting, arm_timeout, "logic analyzer arm")
        return pending

    def record(
        self,
        rate: float,
        samples: int,
        trigger: tuple[int, Slope] | Literal["pattern"] | None = None,
        pretrigger: float = 0.1,
        timeout: float = 5.0,
    ) -> LogicCapture:
        return self.arm(rate, samples, trigger, pretrigger).wait(timeout)

    def record_for(self, seconds: float, rate: float | None = None, **kwargs: Any) -> LogicCapture:
        """Record `seconds`, using the highest rate that fits the buffer (capped by `rate`)."""
        buffer = self.buffer_size
        best = min(rate or self.clock_hz, buffer / seconds)
        return self.record(best, min(buffer, math.ceil(seconds * best)), **kwargs)

    def _status(self, read: bool) -> int:
        state = c_ubyte()
        self._api.FDwfDigitalInStatus(self._h, c_int(int(read)), byref(state))
        return _state_value(state)

    def _collect(self, pending: PendingCapture, timeout: float) -> LogicCapture:
        done = _state_value(self._c.DwfStateDone)
        _poll(lambda: self._status(True) == done, timeout, "logic analyzer capture")
        data = (c_uint16 * pending.samples)()
        self._api.FDwfDigitalInStatusData(self._h, byref(data), c_int(2 * pending.samples))
        return LogicCapture(pending.rate, list(data), pending.trigger_index)

    def stop(self) -> None:
        self._api.FDwfDigitalInConfigure(self._h, c_int(0), c_int(0))


class Wavegen(_Instrument):
    """Analog out W1/W2, limited to the board's safe input range."""

    def _check(self, *volts: float) -> None:
        low, high = self._device.analog_limits
        for value in volts:
            if not low - 1e-9 <= value <= high + 1e-9:
                raise ValueError(f"{value} V is outside the safe range {low}..{high} V")

    def dc(self, channel: int, volts: float) -> None:
        self._check(volts)
        self._setup(channel, self._c.funcDC, 0.0, 0.0, volts, 0.0, None)

    def sine(self, channel: int, low: float, high: float, frequency: float, cycles: float | None = None) -> None:
        self._periodic(channel, self._c.funcSine, low, high, frequency, cycles)

    def triangle(self, channel: int, low: float, high: float, frequency: float, cycles: float | None = None) -> None:
        self._periodic(channel, self._c.funcTriangle, low, high, frequency, cycles)

    def ramp(self, channel: int, low: float, high: float, frequency: float, cycles: float | None = None) -> None:
        self._periodic(channel, self._c.funcRampUp, low, high, frequency, cycles)

    def square(self, channel: int, low: float, high: float, frequency: float, cycles: float | None = None) -> None:
        """Starts with the high half-period; idles at the mid level."""
        self._periodic(channel, self._c.funcSquare, low, high, frequency, cycles)

    def _periodic(self, channel: int, function: Any, low: float, high: float, frequency: float, cycles: float | None) -> None:
        self._check(low, high)
        if high < low:
            raise ValueError("high < low")
        self._setup(channel, function, frequency, (high - low) / 2, (high + low) / 2, 50.0, None if cycles is None else cycles / frequency)

    def _setup(
        self, channel: int, function: Any, frequency: float, amplitude: float, offset: float, symmetry: float, run: float | None
    ) -> None:
        index = c_int(_wavegen_index(channel))
        api, h, c = self._api, self._h, self._c
        node = c.AnalogOutNodeCarrier
        api.FDwfAnalogOutNodeEnableSet(h, index, node, c_int(1))
        api.FDwfAnalogOutNodeFunctionSet(h, index, node, function)
        if frequency:
            api.FDwfAnalogOutNodeFrequencySet(h, index, node, c_double(frequency))
            api.FDwfAnalogOutNodeSymmetrySet(h, index, node, c_double(symmetry))
        api.FDwfAnalogOutNodeAmplitudeSet(h, index, node, c_double(amplitude))
        api.FDwfAnalogOutNodeOffsetSet(h, index, node, c_double(offset))
        api.FDwfAnalogOutIdleSet(h, index, c.DwfAnalogOutIdleOffset)
        api.FDwfAnalogOutRunSet(h, index, c_double(run or 0.0))
        api.FDwfAnalogOutRepeatSet(h, index, c_int(1 if run else 0))
        api.FDwfAnalogOutConfigure(h, index, c_int(1))

    def done(self, channel: int) -> bool:
        state = c_ubyte()
        self._api.FDwfAnalogOutStatus(self._h, c_int(_wavegen_index(channel)), byref(state))
        return _state_value(state) == _state_value(self._c.DwfStateDone)

    def wait_done(self, channel: int, timeout: float = 5.0) -> None:
        _poll(lambda: self.done(channel), timeout, f"wavegen W{channel}")

    def stop(self, channel: int) -> None:
        index = c_int(_wavegen_index(channel))
        self._api.FDwfAnalogOutConfigure(self._h, index, c_int(0))
        self._api.FDwfAnalogOutReset(self._h, index)
        self._api.FDwfAnalogOutConfigure(self._h, index, c_int(0))


class Scope(_Instrument):
    """Analog in: single acquisitions on scope channels 1/2."""

    def acquire(
        self,
        channels: Sequence[int],
        rate: float = 1e5,
        samples: int = 1000,
        range_v: float = 10.0,
        offset_v: float = 0.0,
        timeout: float = 5.0,
    ) -> dict[int, list[float]]:
        api, h, c = self._api, self._h, self._c
        indices = [_scope_index(channel) for channel in channels]
        for index in (0, 1):
            api.FDwfAnalogInChannelEnableSet(h, c_int(index), c_int(int(index in indices)))
        for index in indices:
            api.FDwfAnalogInChannelRangeSet(h, c_int(index), c_double(range_v))
            api.FDwfAnalogInChannelOffsetSet(h, c_int(index), c_double(offset_v))
            api.FDwfAnalogInChannelFilterSet(h, c_int(index), c.filterAverage)
        api.FDwfAnalogInAcquisitionModeSet(h, c.acqmodeSingle)
        api.FDwfAnalogInFrequencySet(h, c_double(rate))
        api.FDwfAnalogInBufferSizeSet(h, c_int(samples))
        api.FDwfAnalogInTriggerSourceSet(h, c.trigsrcNone)
        api.FDwfAnalogInConfigure(h, c_int(1), c_int(0))
        time.sleep(0.05)
        api.FDwfAnalogInConfigure(h, c_int(0), c_int(1))
        done = _state_value(c.DwfStateDone)

        def finished() -> bool:
            state = c_ubyte()
            api.FDwfAnalogInStatus(h, c_int(1), byref(state))
            return _state_value(state) == done

        _poll(finished, timeout, "scope acquisition")
        result: dict[int, list[float]] = {}
        for channel, index in zip(channels, indices):
            buffer = (c_double * samples)()
            api.FDwfAnalogInStatusData(h, c_int(index), byref(buffer), c_int(samples))
            result[channel] = list(buffer)
        return result

    def stats(self, channel: int, rate: float = 1e5, samples: int = 1000) -> analysis.Stats:
        return analysis.stats(self.acquire([channel], rate, samples)[channel])

    def average(self, channel: int, rate: float = 1e5, samples: int = 1000) -> float:
        return self.stats(channel, rate, samples).mean


class ProtocolUart(_Instrument):
    PARITY = {"none": 0, "odd": 1, "even": 2}

    def __init__(self, device: AnalogDiscovery3) -> None:
        super().__init__(device)
        self.parity_errors = 0

    def configure(self, tx: int, rx: int, baud: int, bits: int = 8, parity: Parity = "none", stop: float = 1) -> None:
        api, h = self._api, self._h
        _check_dio(tx)
        _check_dio(rx)
        api.FDwfDigitalUartReset(h)
        api.FDwfDigitalUartRateSet(h, c_double(baud))
        api.FDwfDigitalUartBitsSet(h, c_int(bits))
        api.FDwfDigitalUartParitySet(h, c_int(self.PARITY[parity]))
        api.FDwfDigitalUartStopSet(h, c_double(stop))
        api.FDwfDigitalUartTxSet(h, c_int(tx))
        api.FDwfDigitalUartRxSet(h, c_int(rx))
        api.FDwfDigitalUartTx(h, None, c_int(0))
        count, parity_flag = c_int(), c_int()
        api.FDwfDigitalUartRx(h, None, c_int(0), byref(count), byref(parity_flag))
        self.parity_errors = 0

    def write(self, data: bytes) -> None:
        buffer = create_string_buffer(bytes(data), len(data))
        self._api.FDwfDigitalUartTx(self._h, buffer, c_int(len(data)))

    def _read_chunk(self, size: int = 4096) -> bytes:
        buffer = create_string_buffer(size)
        count, parity_flag = c_int(), c_int()
        self._api.FDwfDigitalUartRx(self._h, buffer, c_int(size), byref(count), byref(parity_flag))
        if parity_flag.value < 0:
            raise InstrumentError("AD3 UART receive buffer overflow")
        if parity_flag.value > 0:
            self.parity_errors += 1
        return buffer.raw[: count.value]

    def read(self, size: int | None = None, timeout: float = 1.0, idle: float = 0.1) -> bytes:
        """Read `size` bytes (or until `idle` seconds without data after the first byte), up to `timeout`."""
        data = bytearray()
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while time.monotonic() < deadline:
            chunk = self._read_chunk()
            now = time.monotonic()
            if chunk:
                data += chunk
                last = now
            if size is not None and len(data) >= size:
                break
            if size is None and data and now - last >= idle:
                break
            time.sleep(0.005)
        return bytes(data)

    def flush(self) -> None:
        while self._read_chunk():
            pass


class ProtocolSpi(_Instrument):
    """SPI master; when the device under test is the master, decode its traffic from a logic capture instead."""

    def configure(self, clk: int, mosi: int, miso: int, cs: int, frequency: float, mode: int = 0, msb_first: bool = True) -> None:
        api, h = self._api, self._h
        self._cs = cs
        api.FDwfDigitalSpiReset(h)
        api.FDwfDigitalSpiFrequencySet(h, c_double(frequency))
        api.FDwfDigitalSpiClockSet(h, c_int(clk))
        api.FDwfDigitalSpiDataSet(h, c_int(0), c_int(mosi))
        api.FDwfDigitalSpiDataSet(h, c_int(1), c_int(miso))
        api.FDwfDigitalSpiModeSet(h, c_int(mode))
        api.FDwfDigitalSpiOrderSet(h, c_int(int(msb_first)))
        api.FDwfDigitalSpiSelect(h, c_int(cs), c_int(1))

    def transfer(self, tx: bytes, rx_len: int | None = None) -> bytes:
        size = len(tx) if rx_len is None else rx_len
        tx_buffer = (c_ubyte * max(1, len(tx)))(*tx)
        rx_buffer = (c_ubyte * max(1, size))()
        api, h = self._api, self._h
        api.FDwfDigitalSpiSelect(h, c_int(self._cs), c_int(0))
        api.FDwfDigitalSpiWriteRead(h, c_int(1), c_int(8), tx_buffer, c_int(len(tx)), rx_buffer, c_int(size))
        api.FDwfDigitalSpiSelect(h, c_int(self._cs), c_int(1))
        return bytes(rx_buffer[:size])


class ProtocolCan(_Instrument):
    """CAN at logic level (TX/RX on DIOs); see the README for a wired-AND bus without transceivers."""

    def __init__(self, device: AnalogDiscovery3) -> None:
        super().__init__(device)
        self.errors: list[int] = []

    def configure(self, tx: int, rx: int, bitrate: int) -> None:
        api, h = self._api, self._h
        _check_dio(tx)
        _check_dio(rx)
        api.FDwfDigitalCanReset(h)
        api.FDwfDigitalCanRateSet(h, c_double(bitrate))
        api.FDwfDigitalCanPolaritySet(h, c_int(0))
        api.FDwfDigitalCanTxSet(h, c_int(tx))
        api.FDwfDigitalCanRxSet(h, c_int(rx))
        api.FDwfDigitalCanTx(h, c_int(-1), c_int(0), c_int(0), c_int(0), None)
        self._receive(0)
        self.errors = []

    def send(self, id: int, data: bytes, ext: bool = False, remote: bool = False) -> None:
        if len(data) > 8:
            raise ValueError("CAN payload is at most 8 bytes")
        buffer = (c_ubyte * 8)(*data)
        self._api.FDwfDigitalCanTx(self._h, c_int(id), c_int(int(ext)), c_int(int(remote)), c_int(len(data)), buffer)

    def _receive(self, size: int) -> tuple[int, CanRxFrame | None]:
        ident, ext, remote, dlc, status = c_int(), c_int(), c_int(), c_int(), c_int()
        buffer = (c_ubyte * 8)()
        self._api.FDwfDigitalCanRx(
            self._h, byref(ident), byref(ext), byref(remote), byref(dlc), buffer if size else None, c_int(size), byref(status)
        )
        if status.value == 1:
            return 1, CanRxFrame(ident.value, bool(ext.value), bool(remote.value), bytes(buffer[: dlc.value]))
        return status.value, None

    def receive(self, timeout: float = 1.0) -> CanRxFrame | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, frame = self._receive(8)
            if frame is not None:
                return frame
            if status >= 2:
                self.errors.append(status)
            time.sleep(0.002)
        return None

    def receive_all(self, duration: float) -> list[CanRxFrame]:
        frames = []
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            frame = self.receive(max(0.0, deadline - time.monotonic()))
            if frame is not None:
                frames.append(frame)
        return frames


@dataclass
class AnalogDiscovery3:
    """Open by serial number (`serial`) or enumeration index (`index`, default first device).

    The WaveForms library is `api_factory()` when given; otherwise the library of the `ad3-bench-server` at
    `remote` (`host[:port]`, default the `AD3_REMOTE` variable, token from `AD3_REMOTE_TOKEN`) when set;
    otherwise the local library.
    """

    serial: str | None = None
    index: int | None = None
    analog_limits: tuple[float, float] = (0.0, 3.3)
    api_factory: Callable[[], Any] | None = None
    remote: str | None = None
    api: Any = field(init=False, default=None)
    handle: c_int = field(init=False, default_factory=c_int)
    _owns_api: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.supplies = Supplies(self)
        self.dio = StaticIo(self)
        self.pattern = PatternGenerator(self)
        self.logic = LogicAnalyzer(self)
        self.wavegen = Wavegen(self)
        self.scope = Scope(self)
        self.uart = ProtocolUart(self)
        self.spi = ProtocolSpi(self)
        self.can = ProtocolCan(self)

    def __enter__(self) -> AnalogDiscovery3:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def list_devices(api: DwfApi) -> list[tuple[int, str, str]]:
        """(index, name, serial) of every connected Digilent device."""
        count = c_int()
        api.FDwfEnum(api.constants.enumfilterAll, byref(count))
        devices = []
        for index in range(count.value):
            name, serial = create_string_buffer(32), create_string_buffer(32)
            api.FDwfEnumDeviceName(c_int(index), name)
            api.FDwfEnumSN(c_int(index), serial)
            devices.append((index, name.value.decode(), serial.value.decode()))
        return devices

    def open(self) -> None:
        if self.api is None:
            self.api = self._create_api()
        try:
            self._open_device()
        except BaseException:
            if not self.handle.value:
                self._release_api()
            raise

    def _open_device(self) -> None:
        devices = self.list_devices(self.api)
        if not devices:
            raise InstrumentError("no Digilent device found")
        index = self.index if self.index is not None else devices[0][0]
        if self.serial is not None:
            wanted = self.serial.upper().removeprefix("SN:")
            matches = [entry for entry in devices if entry[2].upper().removeprefix("SN:") == wanted]
            if not matches:
                raise InstrumentError(f"no device with serial {self.serial} (found {[entry[2] for entry in devices]})")
            index = matches[0][0]
        self.api.FDwfDeviceOpen(c_int(index), byref(self.handle))
        self.api.FDwfDeviceAutoConfigureSet(self.handle, c_int(0))
        log.info("opened device %d of %s", index, devices)
        self.reset_outputs()
        self.supplies.off()

    def _create_api(self) -> Any:
        if self.api_factory is not None:
            return self.api_factory()
        remote = self.remote or os.environ.get("AD3_REMOTE")
        if remote:
            from ..remote import RemoteDwfApi

            self._owns_api = True
            return RemoteDwfApi(remote, token=os.environ.get("AD3_REMOTE_TOKEN"))
        from .dwf import DwfApi

        return DwfApi()

    def reset_outputs(self) -> None:
        """Stop the pattern generator and wavegens, release every static DIO; the supplies are left as they are."""
        self.pattern.stop()
        self.logic.stop()
        self.dio.release_all()
        for channel in (1, 2):
            self.wavegen.stop(channel)

    def close(self) -> None:
        if self.api is None or not self.handle.value:
            return
        try:
            self.reset_outputs()
            self.supplies.off()
        finally:
            try:
                self.api.FDwfDeviceClose(self.handle)
            finally:
                self.handle = c_int()
                self._release_api()

    def _release_api(self) -> None:
        """Close the link to the `ad3-bench-server` when this device opened it."""
        if self._owns_api:
            api, self.api, self._owns_api = self.api, None, False
            api.close()


def _check_dio(dio: int) -> None:
    if not 0 <= dio < DIO_COUNT:
        raise ValueError(f"DIO{dio} does not exist")


def _mask(dios: Sequence[int]) -> int:
    value = 0
    for dio in dios:
        _check_dio(dio)
        value |= 1 << dio
    return value


def _wavegen_index(channel: int) -> int:
    if channel not in (1, 2):
        raise ValueError("wavegen channel must be 1 or 2")
    return channel - 1


def _scope_index(channel: int) -> int:
    if channel not in (1, 2):
        raise ValueError("scope channel must be 1 or 2")
    return channel - 1


def _pack_bits(bits: Sequence[int]) -> Any:
    packed = (c_ubyte * max(1, (len(bits) + 7) // 8))()
    for i, bit in enumerate(bits):
        if bit:
            packed[i // 8] |= 1 << (i % 8)
    return packed


def _poll(condition: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{what} did not finish within {timeout} s")
        time.sleep(0.001)
