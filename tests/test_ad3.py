import pytest

from ad3_waveforms_bench.instruments.ad3 import AnalogDiscovery3, InstrumentError
from ad3_waveforms_bench.instruments.fake import FakeDwfApi, fake_ad3


@pytest.fixture
def api():
    return FakeDwfApi()


@pytest.fixture
def ad3(api):
    device = fake_ad3(api)
    yield device
    device.close()


def test_open_selects_by_serial_and_starts_safe(api):
    device = AnalogDiscovery3(serial="210415abcdef", api_factory=lambda: api)
    device.open()
    assert api.calls_to("FDwfDeviceOpen")
    assert api.calls_to("FDwfAnalogIOEnableSet")[-1][1] == 0
    device.close()
    assert api.calls_to("FDwfDeviceClose")


def test_open_unknown_serial(api):
    with pytest.raises(InstrumentError):
        AnalogDiscovery3(serial="nope", api_factory=lambda: api).open()


def test_supplies_are_explicit(ad3, api):
    ad3.supplies.set(vplus=3.3)
    assert api.calls_to("FDwfAnalogIOEnableSet")[-1][1] == 1
    assert (0, 1, 3.3) in [args[1:] for args in api.calls_to("FDwfAnalogIOChannelNodeSet")]
    with pytest.raises(ValueError):
        ad3.supplies.set(vplus=6.0)


def test_static_io_loopback(ad3, api):
    ad3.dio.drive(3, 1)
    assert ad3.dio.read(3) == 1
    ad3.dio.drive(3, 0)
    assert ad3.dio.read(3) == 0
    ad3.dio.release(3)
    api.inputs = 1 << 3
    assert ad3.dio.read(3) == 1
    with pytest.raises(ValueError):
        ad3.dio.drive(16, 1)


def test_pulses_configure_counter_and_run_time(ad3, api):
    frequency = ad3.pattern.pulses(2, count=10, frequency=1000)
    assert frequency == pytest.approx(1000)
    divider = api.calls_to("FDwfDigitalOutDividerSet")[-1][2]
    low, high = api.calls_to("FDwfDigitalOutCounterSet")[-1][2:]
    assert low == high
    assert 100e6 / divider / (low + high) == pytest.approx(1000)
    assert api.calls_to("FDwfDigitalOutCounterInitSet")[-1][2] == 1
    assert api.calls_to("FDwfDigitalOutRunSet")[-1][1] == pytest.approx(0.01)
    assert api.calls_to("FDwfDigitalOutConfigure")[-1][1] == 1


def test_quadrature_pattern_uses_custom_data(ad3, api):
    frequency = ad3.pattern.quadrature(0, 1, frequency=1000, cycles=25, z=2, index_every=5)
    assert frequency == pytest.approx(1000)
    data = api.calls_to("FDwfDigitalOutDataSet")
    assert [args[1] for args in data] == [0, 1, 2]
    assert all(args[3] == 20 for args in data)
    assert api.calls_to("FDwfDigitalOutRunSet")[-1][1] == pytest.approx(0.025)


def test_custom_rejects_oversized_pattern(ad3, api):
    api.pattern_buffer = 8
    with pytest.raises(ValueError):
        ad3.pattern.custom({0: [0, 1] * 8}, rate=1e6)


def test_logic_capture_with_trigger(ad3, api):
    api.logic_samples = [0b01, 0b11, 0b10, 0b00] * 4
    capture = ad3.logic.record(rate=1e6, samples=16, trigger=(1, "rising"), pretrigger=0.25)
    assert capture.rate == pytest.approx(1e6)
    assert capture.trigger_index == 4
    assert capture.channel(0)[:4] == [1, 1, 0, 0]
    assert capture.edge_count(1, "rising") == 4
    trigger = api.calls_to("FDwfDigitalInTriggerSet")[-1]
    assert trigger[3:] == (2, 0)
    with pytest.raises(ValueError):
        ad3.logic.record(rate=1e6, samples=10**6)


def test_wavegen_limits_and_dc(ad3, api):
    ad3.wavegen.dc(1, 1.65)
    assert api.calls_to("FDwfAnalogOutNodeOffsetSet")[-1][3] == pytest.approx(1.65)
    with pytest.raises(ValueError):
        ad3.wavegen.dc(1, 3.5)
    with pytest.raises(ValueError):
        ad3.wavegen.square(2, -0.5, 1.0, 1000)
    ad3.wavegen.square(2, 1.0, 2.0, 1000, cycles=5)
    assert api.calls_to("FDwfAnalogOutRunSet")[-1][2] == pytest.approx(0.005)
    assert api.calls_to("FDwfAnalogOutNodeAmplitudeSet")[-1][3] == pytest.approx(0.5)


def test_scope_average(ad3, api):
    api.scope_levels[1] = 2.5
    assert ad3.scope.average(2, samples=100) == pytest.approx(2.5)


def test_uart_roundtrip(ad3, api):
    ad3.uart.configure(tx=1, rx=0, baud=115200, parity="even", stop=2)
    assert api.calls_to("FDwfDigitalUartParitySet")[-1][1] == 2
    ad3.uart.write(b"hello")
    assert bytes(api.uart_tx) == b"hello"
    api.uart_rx += b"world"
    assert ad3.uart.read(5, timeout=0.2) == b"world"


def test_can_send_and_receive(ad3, api):
    ad3.can.configure(tx=0, rx=1, bitrate=500000)
    ad3.can.send(0x123, b"\x01\x02", ext=False)
    assert api.can_tx == [(0x123, False, b"\x01\x02")]
    api.can_rx.append((0x1ABCDEF0, True, b"\xaa"))
    frame = ad3.can.receive(timeout=0.2)
    assert (frame.id, frame.ext, frame.data) == (0x1ABCDEF0, True, b"\xaa")
    assert ad3.can.receive(timeout=0.01) is None
