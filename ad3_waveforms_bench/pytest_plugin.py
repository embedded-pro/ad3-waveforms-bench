"""pytest plugin (registered through the `pytest11` entry point): AD3 options, marker and fixture.

Options: `--ad3-serial` (or `AD3_SERIAL`), `--ad3-remote` (or `AD3_REMOTE`), `--no-ad3`, `--fake`. The
session fixture `ad3` opens the device (a `FakeDwfApi` backed one with `--fake`, the one of an
`ad3-bench-server` with `--ad3-remote`) and skips the test when no device is available; tests
marked `ad3` are skipped with `--no-ad3`. Override the `ad3_settings` fixture in a `conftest.py` to set
the analog limits or turn on the supplies.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from .instruments.ad3 import AnalogDiscovery3


@dataclass(frozen=True)
class Ad3Settings:
    """How the `ad3` fixture opens the device; V+/V- stay off when None."""

    analog_limits: tuple[float, float] = (0.0, 3.3)
    vplus: float | None = None
    vminus: float | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ad3-waveforms-bench")
    group.addoption("--ad3-serial", default=os.environ.get("AD3_SERIAL"), help="AD3 serial number (default: first device, or AD3_SERIAL)")
    group.addoption(
        "--ad3-remote",
        default=os.environ.get("AD3_REMOTE"),
        metavar="HOST[:PORT]",
        help="use the AD3 of an ad3-bench-server (default: AD3_REMOTE; token from AD3_REMOTE_TOKEN)",
    )
    group.addoption("--no-ad3", action="store_true", help="skip every test that needs the Analog Discovery 3")
    group.addoption("--fake", action="store_true", help="use the in-memory fakes instead of hardware (no AD3 needed)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "ad3: needs the Analog Discovery 3 (skipped with --no-ad3)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--no-ad3"):
        return
    skip = pytest.mark.skip(reason="--no-ad3")
    for item in items:
        if item.get_closest_marker("ad3"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def ad3_settings() -> Ad3Settings:
    return Ad3Settings()


@pytest.fixture(scope="session")
def ad3(pytestconfig: pytest.Config, ad3_settings: Ad3Settings) -> Iterator[AnalogDiscovery3]:
    if pytestconfig.getoption("--no-ad3"):
        pytest.skip("--no-ad3")
    api_factory: Callable[[], Any] | None = None
    if pytestconfig.getoption("--fake"):
        from .instruments.fake import FakeDwfApi

        api_factory = FakeDwfApi
    device = AnalogDiscovery3(
        serial=pytestconfig.getoption("--ad3-serial"),
        analog_limits=ad3_settings.analog_limits,
        api_factory=api_factory,
        remote=pytestconfig.getoption("--ad3-remote") or None,
    )
    try:
        device.open()
    except (OSError, RuntimeError) as error:
        pytest.skip(f"Analog Discovery 3 unavailable: {error}")
    if ad3_settings.vplus is not None or ad3_settings.vminus is not None:
        device.supplies.set(ad3_settings.vplus, ad3_settings.vminus)
    yield device
    device.close()
