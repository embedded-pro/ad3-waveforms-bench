"""Runs in the container: the `ad3` fixture reaches the host's AD3 through AD3_REMOTE, the terminal
reaches the host's serial port through the RFC2217 URL in DUT_PORT."""

import os

import pytest

from ad3_waveforms_bench import FirmwareTerminal

pytestmark = pytest.mark.ad3


@pytest.fixture(scope="session")
def terminal():
    with FirmwareTerminal(os.environ["DUT_PORT"], baud=115200) as device:
        device.sync()
        yield device


def test_loopback(ad3):
    ad3.dio.drive(0, 1)
    assert ad3.dio.read(0) == 1
    ad3.dio.release(0)


def test_device_answers(terminal):
    assert terminal.command("ping").ok
