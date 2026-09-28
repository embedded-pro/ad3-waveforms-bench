"""Pure signal analysis on sampled logic levels (lists of 0/1) and analog samples.

Times are in seconds; `rate` is the sample rate in Hz. Everything here is hardware independent.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

Bits = Sequence[int]
EdgeKind = Literal["rising", "falling", "both"]


@dataclass(frozen=True)
class Edge:
    index: int
    rising: bool


def edges(bits: Bits) -> list[Edge]:
    """Edges at the index of the first sample of the new level."""
    return [Edge(i, bool(bits[i])) for i in range(1, len(bits)) if bits[i] != bits[i - 1]]


def edge_count(bits: Bits, kind: EdgeKind = "both") -> int:
    found = edges(bits)
    if kind == "both":
        return len(found)
    return sum(1 for edge in found if edge.rising == (kind == "rising"))


def runs(bits: Bits) -> list[tuple[int, int, int]]:
    """Consecutive runs as (level, start index, length)."""
    result: list[tuple[int, int, int]] = []
    start = 0
    for i in range(1, len(bits) + 1):
        if i == len(bits) or bits[i] != bits[start]:
            result.append((int(bits[start]), start, i - start))
            start = i
    return result


def high_low_times(bits: Bits, rate: float) -> tuple[list[float], list[float]]:
    """Durations of complete high and low pulses (runs touching either end of the capture are dropped)."""
    complete = runs(bits)[1:-1]
    highs = [length / rate for level, _, length in complete if level]
    lows = [length / rate for level, _, length in complete if not level]
    return highs, lows


def frequency(bits: Bits, rate: float) -> float:
    """Mean frequency from the first to the last rising edge; 0 when fewer than two rising edges."""
    rising = [edge.index for edge in edges(bits) if edge.rising]
    if len(rising) < 2:
        return 0.0
    return (len(rising) - 1) * rate / (rising[-1] - rising[0])


def duty_cycle(bits: Bits) -> float:
    """High fraction (0..1) over whole periods between the first and last rising edge.

    A constant signal returns its level (0.0 or 1.0).
    """
    rising = [edge.index for edge in edges(bits) if edge.rising]
    if len(rising) < 2:
        return float(sum(bits) / len(bits)) if bits and not edges(bits) else math.nan
    window = bits[rising[0] : rising[-1]]
    return sum(window) / len(window)


def pulse_centers(bits: Bits, rate: float) -> list[float]:
    """Mid-point time of each complete high pulse."""
    return [(start + length / 2) / rate for level, start, length in runs(bits)[1:-1] if level]


def rising_times(bits: Bits, rate: float) -> list[float]:
    return [edge.index / rate for edge in edges(bits) if edge.rising]


def falling_times(bits: Bits, rate: float) -> list[float]:
    return [edge.index / rate for edge in edges(bits) if not edge.rising]


def nearest_offsets(reference: Sequence[float], other: Sequence[float]) -> list[float]:
    """For each time in `reference`, the signed offset to the closest time in `other`."""
    if not other:
        return []
    result = []
    for time in reference:
        closest = min(other, key=lambda candidate: abs(candidate - time))
        result.append(closest - time)
    return result


def phase_between(a: Bits, b: Bits, rate: float) -> tuple[float, float]:
    """(seconds, degrees) from each rising edge of `a` to the next rising edge of `b`, averaged."""
    rising_a = rising_times(a, rate)
    rising_b = rising_times(b, rate)
    period = 1 / frequency(a, rate) if frequency(a, rate) else math.nan
    delays = []
    for time in rising_a:
        later = [candidate for candidate in rising_b if candidate >= time]
        if later:
            delays.append(later[0] - time)
    if not delays:
        return math.nan, math.nan
    delay = statistics.fmean(delays)
    degrees = math.nan if math.isnan(period) else (delay / period * 360.0) % 360.0
    return delay, degrees


def overlap_samples(a: Bits, b: Bits, active_high: bool = True) -> int:
    """Samples where both outputs are active at once (shoot-through for a half bridge)."""
    active = 1 if active_high else 0
    return sum(1 for x, y in zip(a, b) if x == active and y == active)


def dead_times(a: Bits, b: Bits, rate: float, active_high: bool = True) -> list[float]:
    """Durations where both outputs are inactive between one output turning off and the other turning on."""
    inactive = 0 if active_high else 1
    both_off = [1 if (x == inactive and y == inactive) else 0 for x, y in zip(a, b)]
    result = []
    for level, start, length in runs(both_off)[1:-1]:
        if not level:
            continue
        before, after = start - 1, start + length
        a_to_b = a[before] != inactive and b[after] != inactive
        b_to_a = b[before] != inactive and a[after] != inactive
        if a_to_b or b_to_a:
            result.append(length / rate)
    return result


@dataclass(frozen=True)
class DeadTimeSplit:
    a_off_to_b_on: list[float]
    b_off_to_a_on: list[float]


def dead_times_split(a: Bits, b: Bits, rate: float, active_high: bool = True) -> DeadTimeSplit:
    inactive = 0 if active_high else 1
    both_off = [1 if (x == inactive and y == inactive) else 0 for x, y in zip(a, b)]
    a_b: list[float] = []
    b_a: list[float] = []
    for level, start, length in runs(both_off)[1:-1]:
        if not level:
            continue
        before, after = start - 1, start + length
        if a[before] != inactive and b[after] != inactive:
            a_b.append(length / rate)
        elif b[before] != inactive and a[after] != inactive:
            b_a.append(length / rate)
    return DeadTimeSplit(a_b, b_a)


@dataclass(frozen=True)
class QuadratureResult:
    count: int
    errors: int
    index_pulses: int

    @property
    def direction(self) -> str:
        return "fwd" if self.count >= 0 else "rev"


_QUAD_STEP = {
    (0, 2): 1,
    (2, 3): 1,
    (3, 1): 1,
    (1, 0): 1,
    (2, 0): -1,
    (3, 2): -1,
    (1, 3): -1,
    (0, 1): -1,
}


def quadrature_decode(a: Bits, b: Bits, z: Bits | None = None) -> QuadratureResult:
    """4x decode; positive count when A leads B. Invalid double transitions are counted as errors."""
    count = 0
    errors = 0
    previous = (a[0] << 1) | b[0] if a else 0
    for x, y in zip(a[1:], b[1:]):
        state = (x << 1) | y
        if state != previous:
            step = _QUAD_STEP.get((previous, state))
            if step is None:
                errors += 1
            else:
                count += step
            previous = state
    index_pulses = edge_count(z, "rising") if z is not None else 0
    return QuadratureResult(count, errors, index_pulses)


def quadrature_pattern(
    cycles: int,
    direction: Literal["fwd", "rev"] = "fwd",
    index_every: int | None = None,
) -> dict[str, list[int]]:
    """Samples at 4x the encoder frequency for `cycles` full cycles; A leads B for `fwd`.

    Z is high for the first quarter of every `index_every`-th cycle (starting with the first).
    """
    forward = [(1, 0), (1, 1), (0, 1), (0, 0)]
    sequence = forward if direction == "fwd" else [(b, a) for a, b in forward]
    a_bits: list[int] = []
    b_bits: list[int] = []
    z_bits: list[int] = []
    for cycle in range(cycles):
        for step, (a_level, b_level) in enumerate(sequence):
            a_bits.append(a_level)
            b_bits.append(b_level)
            z_bits.append(1 if index_every and cycle % index_every == 0 and step == 0 else 0)
    return {"a": a_bits, "b": b_bits, "z": z_bits}


@dataclass
class SpiFrame:
    mosi: bytearray = field(default_factory=bytearray)
    miso: bytearray = field(default_factory=bytearray)
    clock_edges: int = 0


def spi_mode_bits(mode: int) -> tuple[int, int]:
    """(CPOL, CPHA) for SPI mode 0-3."""
    if mode not in (0, 1, 2, 3):
        raise ValueError(f"SPI mode {mode}")
    return mode >> 1, mode & 1


def spi_decode(
    clk: Bits,
    mosi: Bits,
    miso: Bits | None = None,
    cs: Bits | None = None,
    mode: int = 0,
    bits_per_word: int = 8,
    msb_first: bool = True,
    cs_active_low: bool = True,
) -> list[SpiFrame]:
    """Decode SPI from a logic capture. Each chip-select-active region is one frame; without `cs` the
    whole capture is one frame. Partial trailing words are dropped."""
    cpol, cpha = spi_mode_bits(mode)
    sample_on_rising = cpol == cpha
    active = 0 if cs_active_low else 1
    frames: list[SpiFrame] = []
    whole = [(0, len(clk))]
    regions = whole if cs is None else [(start, start + length) for level, start, length in runs(cs) if level == active]
    for start, end in regions:
        frame = SpiFrame()
        word_mosi = word_miso = 0
        nbits = 0
        for i in range(max(start, 1), end):
            if clk[i] == clk[i - 1] or bool(clk[i]) != sample_on_rising:
                continue
            frame.clock_edges += 1
            bit_mosi = int(mosi[i])
            bit_miso = int(miso[i]) if miso is not None else 0
            if msb_first:
                word_mosi = (word_mosi << 1) | bit_mosi
                word_miso = (word_miso << 1) | bit_miso
            else:
                word_mosi |= bit_mosi << nbits
                word_miso |= bit_miso << nbits
            nbits += 1
            if nbits == bits_per_word:
                frame.mosi.append(word_mosi & 0xFF)
                frame.miso.append(word_miso & 0xFF)
                word_mosi = word_miso = 0
                nbits = 0
        if frame.clock_edges:
            frames.append(frame)
    return frames


def spi_join(frames: Sequence[SpiFrame]) -> tuple[bytes, bytes]:
    """Concatenate frames (TI SSI pulses FSS between words when SPH=0)."""
    return b"".join(bytes(frame.mosi) for frame in frames), b"".join(bytes(frame.miso) for frame in frames)


def clock_idle_level(clk: Bits, cs: Bits | None = None, cs_active_low: bool = True) -> int:
    """Clock level just before the first chip-select assertion (or the first sample)."""
    if cs is not None:
        active = 0 if cs_active_low else 1
        for i in range(1, len(cs)):
            if cs[i] == active and cs[i - 1] != active:
                return int(clk[i - 1])
    return int(clk[0])


@dataclass(frozen=True)
class UartByte:
    value: int
    parity_error: bool
    framing_error: bool
    start_index: int


def uart_decode(
    bits: Bits,
    rate: float,
    baud: float,
    data_bits: int = 8,
    parity: Literal["none", "even", "odd"] = "none",
    stop_bits: int = 1,
) -> list[UartByte]:
    """Decode idle-high UART by sampling in the middle of each bit after the start-bit falling edge."""
    per_bit = rate / baud
    if per_bit < 3:
        raise ValueError(f"sample rate {rate} too low for {baud} baud")
    frame_bits = 1 + data_bits + (parity != "none") + stop_bits
    result: list[UartByte] = []
    i = 1
    while i < len(bits):
        if not (bits[i - 1] and not bits[i]):
            i += 1
            continue
        end = i + int(round(frame_bits * per_bit))
        if end > len(bits):
            break

        def sample(bit: int, start: int = i) -> int:
            return int(bits[start + int((bit + 0.5) * per_bit)])

        value = sum(sample(1 + n) << n for n in range(data_bits))
        parity_error = False
        next_bit = 1 + data_bits
        if parity != "none":
            ones = bin(value).count("1") + sample(next_bit)
            parity_error = (ones % 2 == 1) != (parity == "odd")
            next_bit += 1
        framing_error = any(sample(next_bit + n) == 0 for n in range(stop_bits))
        result.append(UartByte(value, parity_error, framing_error, i))
        i += int(round((next_bit + stop_bits - 0.5) * per_bit))
    return result


def adc_code(volts: float, vref: float = 3.3, bits: int = 12) -> int:
    full = (1 << bits) - 1
    return max(0, min(full, round(volts / vref * (1 << bits))))


def adc_volts(code: float, vref: float = 3.3, bits: int = 12) -> float:
    return code * vref / (1 << bits)


@dataclass(frozen=True)
class Stats:
    mean: float
    minimum: float
    maximum: float
    stdev: float
    count: int


def stats(samples: Sequence[float]) -> Stats:
    if not samples:
        raise ValueError("no samples")
    return Stats(
        mean=statistics.fmean(samples),
        minimum=min(samples),
        maximum=max(samples),
        stdev=statistics.pstdev(samples),
        count=len(samples),
    )


def deinterleave(samples: Sequence[int], channels: int) -> list[list[int]]:
    """Split `[c0, c1, c0, c1, ...]` into one list per channel."""
    return [list(samples[i::channels]) for i in range(channels)]


def pick_sample_rate(
    signal_hz: float,
    periods: float,
    max_rate: float,
    buffer_size: int,
    min_samples_per_period: float = 20,
) -> float:
    """Highest usable rate that still fits `periods` of the signal into the buffer."""
    fit = buffer_size * signal_hz / periods
    rate = min(max_rate, fit)
    if rate < signal_hz * min_samples_per_period:
        raise ValueError(f"{periods} periods of {signal_hz} Hz do not fit {buffer_size} samples at a useful rate")
    return rate
