import pytest

from ad3_waveforms_bench.protocol import (
    Dialect,
    ProtocolError,
    format_command,
    format_hex,
    format_value,
    is_event_line,
    is_final_line,
    parse_event,
    parse_hex,
    parse_number,
    parse_response,
)


def test_ok_with_values():
    response = parse_response("OK pos=0x10 dir=fwd speed=12 res=4095")
    assert response.ok
    assert response.reason is None
    assert response.as_int("pos") == 16
    assert response["dir"] == "fwd"
    assert response.as_int("res") == 4095


def test_err_reason():
    response = parse_response("ERR busy")
    assert not response.ok
    assert response.reason == "busy"


def test_unrecognized_command_is_an_error():
    response = parse_response("Unrecognized command.")
    assert not response.ok
    assert response.reason == "unrecognized"


def test_typed_helpers():
    response = parse_response("OK samples=1,2,0x10 data=a55a0102 empty=")
    assert response.as_ints("samples") == [1, 2, 16]
    assert response.as_bytes("data") == b"\xa5\x5a\x01\x02"
    assert response.as_list("empty") == []
    assert response.as_bytes("empty") == b""
    with pytest.raises(KeyError):
        response["missing"]


def test_event():
    event = parse_event("EVT can index=0 id=0x123 ext=0 data=0102")
    assert event.kind == "can"
    assert event.as_int("id") == 0x123
    assert not event.as_bool("ext")
    assert event.as_bytes("data") == b"\x01\x02"


def test_line_classification():
    assert is_final_line("OK")
    assert is_final_line("OK value=1")
    assert is_final_line("ERR usage")
    assert not is_final_line("OKAY")
    assert is_event_line("EVT boot board=x")
    assert not is_event_line("EVTX")


@pytest.mark.parametrize(
    ("text", "value"),
    [("0", 0), ("42", 42), ("0x2A", 42), ("0x7ff", 0x7FF), ("-5", -5)],
)
def test_numbers(text, value):
    assert parse_number(text) == value


def test_bad_number_and_hex():
    with pytest.raises(ProtocolError):
        parse_number("12a")
    with pytest.raises(ProtocolError):
        parse_hex("abc")
    assert parse_hex("-") == b""


def test_format_command():
    line = format_command("pwm.open", 0, gens=[0, 1, 2], freq=20000, dead="off", inva=True, sync=None)
    assert line == "pwm.open 0 gens=0,1,2 freq=20000 dead=off inva=1"


def test_format_values():
    assert format_value(12.5) == "12.5"
    assert format_value(100.0) == "100"
    assert format_value(0.0) == "0"
    assert format_value(b"\x00\xff") == "00ff"
    assert format_value(b"") == "-"
    assert format_command("spi.xfer", 0, b"\xa5", continue_=False) == "spi.xfer 0 a5 continue=0"


def test_format_rejects_spaces():
    with pytest.raises(ProtocolError):
        format_command("gpio.cfg", "pin 1", "out")


def test_hex_roundtrip():
    assert format_hex(b"\x01\xab") == "01ab"
    assert parse_hex("01ab") == b"\x01\xab"


def test_err_without_reason_uses_default():
    assert parse_response("ERR").reason == "failed"
    assert parse_response("ERR", Dialect(default_error_reason="error")).reason == "error"


def test_custom_dialect():
    dialect = Dialect(ok="+OK", err="-ERR", event="!", unrecognized=("?",))
    assert is_final_line("+OK a=1", dialect)
    assert is_final_line("-ERR busy", dialect)
    assert is_final_line("?", dialect)
    assert not is_final_line("OK", dialect)
    assert is_event_line("! tick n=1", dialect)
    assert not is_event_line("EVT tick", dialect)
    assert parse_response("+OK a=1", dialect).as_int("a") == 1
    assert parse_response("-ERR busy", dialect).reason == "busy"
    assert parse_response("?", dialect).reason == "unrecognized"
    assert parse_event("! tick n=2", dialect).as_int("n") == 2
    with pytest.raises(ProtocolError):
        parse_response("OK", dialect)
    with pytest.raises(ProtocolError):
        parse_event("EVT tick", dialect)
