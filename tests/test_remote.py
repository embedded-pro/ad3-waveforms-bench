import ctypes
import socket
import threading
import time
from ctypes import byref, c_char, c_double, c_int, c_ubyte, c_uint, c_uint16, create_string_buffer

import pytest

from ad3_waveforms_bench.instruments.ad3 import AnalogDiscovery3
from ad3_waveforms_bench.instruments.dwf import DwfError
from ad3_waveforms_bench.instruments.fake import FakeDwfApi
from ad3_waveforms_bench.remote import RemoteDwfApi, RemoteError, parse_address
from ad3_waveforms_bench.remote.server import Ad3Server, main
from ad3_waveforms_bench.remote.wire import Decoded, WireError, apply_outputs, encode_arg, type_code


def _roundtrip(args, server_side):
    """Encode `args`, let `server_side` mutate the rebuilt objects, write the outputs back."""
    decoded = Decoded([encode_arg(arg) for arg in args])
    server_side(*decoded.call_args())
    apply_outputs(tuple(args), decoded.outputs())


def test_type_codes_are_platform_independent():
    assert type_code(c_int) == "i4"
    assert type_code(c_uint) == "u4"
    assert type_code(c_uint16) == "u2"
    assert type_code(c_ubyte) == "u1"
    assert type_code(c_double) == "f8"
    assert type_code(c_char) == "c1"
    assert type_code(ctypes.c_uint64) == "u8"


def test_values_are_passed_and_byref_values_written_back():
    count, rate = c_int(3), c_double()

    def server(value, count_ref, rate_ref):
        assert value.value == 7
        ctypes.cast(count_ref, ctypes.POINTER(c_int)).contents.value = 11
        ctypes.cast(rate_ref, ctypes.POINTER(c_double)).contents.value = 1.5e6

    _roundtrip((c_uint(7), byref(count), byref(rate)), server)
    assert (count.value, rate.value) == (11, 1.5e6)


def test_arrays_are_passed_and_written_back():
    samples = (c_uint16 * 4)()
    tx = (c_ubyte * 3)(1, 2, 3)
    name = create_string_buffer(16)

    def server(samples_ref, tx_array, name_buffer, nothing):
        assert bytes(tx_array) == b"\x01\x02\x03"
        assert nothing is None
        target = ctypes.cast(samples_ref, ctypes.POINTER(c_uint16 * 4)).contents
        target[:] = [1, 0xFFFF, 3, 4]
        name_buffer.value = b"Analog Discovery 3"[:15]

    _roundtrip((byref(samples), tx, name, None), server)
    assert list(samples) == [1, 0xFFFF, 3, 4]
    assert name.value == b"Analog Discover"
    assert list(tx) == [1, 2, 3]


def test_unchanged_and_zero_arrays_are_not_sent():
    buffer = (c_ubyte * 1024)()
    encoded = encode_arg(buffer)
    assert encoded["b"] is None
    decoded = Decoded([encoded])
    assert decoded.outputs() == [None]


def test_unsupported_arguments_are_rejected():
    with pytest.raises(TypeError):
        encode_arg(5)
    with pytest.raises(TypeError):
        encode_arg(ctypes.c_char_p(b"x"))
    with pytest.raises(WireError):
        Decoded([{"t": "x9", "v": 0}])
    with pytest.raises(WireError):
        Decoded([{"arr": "u1", "n": 2, "b": "AAAA"}])


def test_parse_address():
    assert parse_address("host.docker.internal") == ("host.docker.internal", 5025)
    assert parse_address("10.0.0.2:6000") == ("10.0.0.2", 6000)
    assert parse_address("[::1]:7000") == ("::1", 7000)


@pytest.fixture
def backend():
    return FakeDwfApi()


@pytest.fixture
def server(backend):
    with Ad3Server(lambda: backend, port=0) as running:
        yield running


def _address(service):
    host, port = service.address
    return f"{host}:{port}"


def _wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


def test_remote_device_behaves_like_a_local_one(server, backend):
    backend.logic_samples = [0b01, 0b10] * 200
    backend.uart_rx += b"pong"
    backend.can_rx.append((0x123, False, b"\x01\x02"))
    backend.scope_levels[1] = 1.25
    with AnalogDiscovery3(serial="210415ABCDEF", remote=_address(server)) as ad3:
        assert ad3.pattern.pulses(2, count=10, frequency=1000) == pytest.approx(1000)
        ad3.dio.drive(3, 1)
        assert ad3.dio.read(3) == 1
        capture = ad3.logic.record(rate=1e6, samples=400, trigger=(0, "rising"))
        assert capture.samples[:4] == [1, 2, 1, 2]
        assert capture.frequency(0) == pytest.approx(500e3)
        ad3.uart.configure(tx=1, rx=0, baud=115200)
        ad3.uart.write(b"ping")
        assert ad3.uart.read(4, timeout=1.0) == b"pong"
        ad3.can.configure(tx=4, rx=5, bitrate=500_000)
        ad3.can.send(0x42, b"\xaa")
        frame = ad3.can.receive(timeout=1.0)
        assert (frame.id, frame.data) == (0x123, b"\x01\x02")
        assert ad3.scope.average(2, samples=100) == pytest.approx(1.25)
        assert ad3.api.constants.DwfStateDone.value == 2
    assert backend.uart_tx == b"ping"
    assert backend.can_tx == [(0x42, False, b"\xaa")]
    assert backend.calls_to("FDwfDeviceClose")
    assert ad3.api is None


def test_remote_calls_match_local_calls(server, backend):
    local = FakeDwfApi()
    for api_factory in (lambda: local, None):
        device = AnalogDiscovery3(api_factory=api_factory, remote=_address(server))
        device.open()
        device.pattern.clock(3, frequency=1e6, duty=0.25)
        device.wavegen.sine(1, low=0.5, high=2.5, frequency=1000)
        device.close()
    assert _normalized(backend.calls) == _normalized(local.calls)


def _normalized(calls):
    return [(name, tuple(bytes(arg) if isinstance(arg, ctypes.Array) else arg for arg in args)) for name, args in calls]


def test_errors_are_raised_as_dwf_errors(server, backend):
    def fail(*args):
        raise DwfError("FDwfDigitalOutConfigure: device busy")

    backend._FDwfDigitalOutConfigure = fail
    with RemoteDwfApi(_address(server)) as api, pytest.raises(DwfError, match="device busy"):
        api.FDwfDigitalOutConfigure(c_int(1), c_int(1))


def test_only_waveforms_functions_are_forwarded(server):
    with RemoteDwfApi(_address(server)) as api:
        with pytest.raises(AttributeError):
            api.system  # noqa: B018
        with pytest.raises(DwfError, match="not a WaveForms function"):
            api._request({"op": "call", "name": "FDwf.__class__", "args": []})


def test_disconnect_returns_the_device_to_the_safe_state(server, backend):
    api = RemoteDwfApi(_address(server))
    device = AnalogDiscovery3(api_factory=lambda: api)
    device.open()
    device.supplies.set(vplus=3.3)
    device.dio.drive(0, 1)
    backend.calls.clear()
    api.close()
    _wait_for(lambda: backend.calls_to("FDwfDeviceClose"))
    assert backend.calls_to("FDwfAnalogIOEnableSet")[-1][1] == 0
    assert backend.calls_to("FDwfDigitalIOOutputEnableSet")[-1][1] == 0


def test_explicit_close_does_not_close_twice(server, backend):
    with AnalogDiscovery3(remote=_address(server)):
        pass
    time.sleep(0.1)
    assert len(backend.calls_to("FDwfDeviceClose")) == 1


def test_token(backend):
    with Ad3Server(lambda: backend, port=0, token="s3cret") as server:
        with pytest.raises(RemoteError, match="invalid token"):
            RemoteDwfApi(_address(server), token="wrong")
        with RemoteDwfApi(_address(server), token="s3cret") as api:
            assert api.constants.DwfStateDone.value == 2


def test_one_client_at_a_time(server):
    with RemoteDwfApi(_address(server)), pytest.raises(RemoteError, match="busy"):
        RemoteDwfApi(_address(server))
    _wait_for(lambda: not server._busy.locked())
    with RemoteDwfApi(_address(server)):
        pass


def test_missing_library_is_reported():
    def missing():
        raise OSError("dwf.dll not found")

    with Ad3Server(missing, port=0) as server, pytest.raises(RemoteError, match="dwf.dll not found"):
        RemoteDwfApi(_address(server))


def test_unreachable_server_raises_oserror():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(OSError):
        RemoteDwfApi(f"127.0.0.1:{port}", timeout=1.0)


def test_terminal_accepts_socket_urls():
    """A byte-stream bridge (such as port-bridge) is opened as `socket://host:port`."""
    from ad3_waveforms_bench.terminal import FirmwareTerminal

    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]

        def echo():
            connection, _ = listener.accept()
            with connection:
                while data := connection.recv(1024):
                    connection.sendall(data)

        thread = threading.Thread(target=echo, daemon=True)
        thread.start()
        with FirmwareTerminal(f"socket://127.0.0.1:{port}", timeout=1.0) as terminal:
            terminal.write_raw(b"OK value=1\r\n")
            assert terminal.pump(0.5) >= 1
        thread.join(timeout=2)


def test_server_cli_help(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "--token" in capsys.readouterr().out


def test_pytest_option(pytester: pytest.Pytester, server):
    pytester.makepyfile(
        """
def test_device(ad3):
    ad3.dio.drive(2, 1)
    assert ad3.dio.read(2) == 1
    assert type(ad3.api).__name__ == "RemoteDwfApi"
"""
    )
    pytester.runpytest("--ad3-remote", _address(server)).assert_outcomes(passed=1)
