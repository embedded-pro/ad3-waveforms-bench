import math

import pytest

from ad3_waveforms_bench import analysis


def square(period, high, cycles, lead=5):
    bits = [0] * lead
    for _ in range(cycles):
        bits += [1] * high + [0] * (period - high)
    return bits


def complementary(period, duty_samples, dead, cycles):
    """A high for duty-dead, B high for the rest minus dead, dead samples of both low in between."""
    a, b = [], []
    for _ in range(cycles):
        a += [0] * dead + [1] * (duty_samples - dead) + [0] * (period - duty_samples)
        b += [0] * duty_samples + [0] * dead + [1] * (period - duty_samples - dead)
    return a, b


def test_frequency_duty_and_edges():
    bits = square(period=100, high=25, cycles=10)
    rate = 1e6
    assert analysis.frequency(bits, rate) == pytest.approx(10_000)
    assert analysis.duty_cycle(bits) == pytest.approx(0.25)
    assert analysis.edge_count(bits, "rising") == 10
    assert analysis.edge_count(bits, "falling") == 10
    assert analysis.edge_count(bits) == 20
    highs, lows = analysis.high_low_times(bits, rate)
    assert set(highs) == {25e-6}
    assert set(lows) == {75e-6}


def test_constant_signal():
    assert analysis.duty_cycle([1] * 50) == 1.0
    assert analysis.duty_cycle([0] * 50) == 0.0
    assert analysis.frequency([0] * 50, 1e6) == 0.0


def test_dead_times_and_overlap():
    a, b = complementary(period=100, duty_samples=40, dead=5, cycles=6)
    times = analysis.dead_times(a, b, 1e8)
    assert times
    assert all(t == pytest.approx(5e-8) for t in times)
    split = analysis.dead_times_split(a, b, 1e8)
    assert split.a_off_to_b_on and split.b_off_to_a_on
    assert analysis.overlap_samples(a, b) == 0
    assert analysis.overlap_samples([1, 1, 0], [1, 0, 0]) == 1


def test_phase_and_alignment():
    rate = 1e6
    a = square(100, 50, 10, lead=10)
    b = square(100, 50, 10, lead=35)
    delay, degrees = analysis.phase_between(a, b, rate)
    assert delay == pytest.approx(25e-6)
    assert degrees == pytest.approx(90.0)
    centers = analysis.pulse_centers(a, rate)
    assert centers[1] - centers[0] == pytest.approx(100e-6)
    offsets = analysis.nearest_offsets(analysis.rising_times(a, rate), analysis.rising_times(b, rate))
    assert all(offset == pytest.approx(25e-6) for offset in offsets)


@pytest.mark.parametrize("direction", ["fwd", "rev"])
@pytest.mark.parametrize("cycles", [1, 7, 100])
def test_quadrature_roundtrip(direction, cycles):
    pattern = analysis.quadrature_pattern(cycles, direction, index_every=4)
    a, b, z = [0] + pattern["a"], [0] + pattern["b"], [0] + pattern["z"]
    result = analysis.quadrature_decode(a, b, z)
    expected = 4 * cycles
    assert result.count == (expected if direction == "fwd" else -expected)
    assert result.errors == 0
    assert result.direction == direction
    assert result.index_pulses == math.ceil(cycles / 4)


def test_quadrature_counts_invalid_transitions():
    assert analysis.quadrature_decode([0, 1], [0, 1]).errors == 1


def spi_waveform(mosi_bytes, miso_bytes, mode, spb=4, cs_gap_per_word=False):
    cpol, cpha = analysis.spi_mode_bits(mode)
    clk, mosi, miso, cs = [cpol] * 8, [0] * 8, [0] * 8, [1] * 8

    def emit(c, mo, mi, s, n):
        clk.extend([c] * n)
        mosi.extend([mo] * n)
        miso.extend([mi] * n)
        cs.extend([s] * n)

    for index, (out_byte, in_byte) in enumerate(zip(mosi_bytes, miso_bytes)):
        if index == 0 or cs_gap_per_word:
            emit(cpol, 0, 0, 1, spb)
        bits = [((out_byte >> (7 - i)) & 1, (in_byte >> (7 - i)) & 1) for i in range(8)]
        for out_bit, in_bit in bits:
            if cpha == 0:
                emit(cpol, out_bit, in_bit, 0, spb)
                emit(1 - cpol, out_bit, in_bit, 0, spb)
            else:
                emit(1 - cpol, out_bit, in_bit, 0, spb)
                emit(cpol, out_bit, in_bit, 0, spb)
    emit(cpol, 0, 0, 1, spb * 2)
    return clk, mosi, miso, cs


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize("gap", [False, True])
def test_spi_decode_all_modes(mode, gap):
    tx, rx = b"\xa5\x01\xff", b"\x5a\x80\x00"
    clk, mosi, miso, cs = spi_waveform(tx, rx, mode, cs_gap_per_word=gap)
    frames = analysis.spi_decode(clk, mosi, miso, cs, mode)
    assert analysis.spi_join(frames) == (tx, rx)
    assert len(frames) == (3 if gap else 1)
    assert analysis.clock_idle_level(clk, cs) == analysis.spi_mode_bits(mode)[0]


def test_spi_decode_without_chip_select():
    clk, mosi, miso, _ = spi_waveform(b"\x3c", b"\xc3", 0)
    frames = analysis.spi_decode(clk, mosi, miso, None, 0)
    assert analysis.spi_join(frames) == (b"\x3c", b"\xc3")


def uart_waveform(data, per_bit, parity="none", stop_bits=1):
    bits = [1] * (per_bit * 3)
    for byte in data:
        frame = [0] + [(byte >> i) & 1 for i in range(8)]
        if parity != "none":
            ones = bin(byte).count("1")
            frame.append(ones % 2 if parity == "even" else 1 - ones % 2)
        frame += [1] * stop_bits
        for bit in frame:
            bits += [bit] * per_bit
    return bits + [1] * per_bit * 2


@pytest.mark.parametrize("parity", ["none", "even", "odd"])
@pytest.mark.parametrize("stop_bits", [1, 2])
def test_uart_decode(parity, stop_bits):
    data = b"\x00\x55\xff\xa5Hi"
    bits = uart_waveform(data, per_bit=10, parity=parity, stop_bits=stop_bits)
    decoded = analysis.uart_decode(bits, rate=1_152_000, baud=115_200, parity=parity, stop_bits=stop_bits)
    assert bytes(item.value for item in decoded) == data
    assert not any(item.parity_error or item.framing_error for item in decoded)


def test_uart_decode_detects_parity_error():
    bits = uart_waveform(b"\x01", per_bit=10, parity="even")
    decoded = analysis.uart_decode(bits, rate=1e6, baud=1e5, parity="odd")
    assert decoded[0].parity_error


def test_adc_helpers_and_stats():
    assert analysis.adc_code(0.0) == 0
    assert analysis.adc_code(1.65) == 2048
    assert analysis.adc_code(3.3) == 4095
    assert analysis.adc_volts(2048) == pytest.approx(1.65)
    result = analysis.stats([1, 2, 3, 4])
    assert (result.mean, result.minimum, result.maximum, result.count) == (2.5, 1, 4, 4)
    assert analysis.deinterleave([1, 10, 2, 20, 3, 30], 2) == [[1, 2, 3], [10, 20, 30]]


def test_pick_sample_rate():
    assert analysis.pick_sample_rate(20_000, 20, 100e6, 32768) == pytest.approx(32768 * 1000)
    assert analysis.pick_sample_rate(1_000_000, 10, 100e6, 32768) == 100e6
    with pytest.raises(ValueError):
        analysis.pick_sample_rate(10, 10_000, 100e6, 1000)
