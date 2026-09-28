import pytest

TESTS = """
import pytest


@pytest.mark.ad3
def test_marked():
    pass


def test_device(ad3):
    ad3.dio.drive(2, 1)
    assert ad3.dio.read(2) == 1
    assert ad3.analog_limits == (0.0, 3.3)
"""


def test_plugin_is_registered(pytestconfig):
    assert pytestconfig.pluginmanager.has_plugin("ad3_waveforms_bench")


def test_help_lists_options(pytester: pytest.Pytester):
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--ad3-serial*", "*--no-ad3*", "*--fake*"])


def test_fake_device(pytester: pytest.Pytester):
    pytester.makepyfile(TESTS)
    pytester.runpytest("--fake", "--strict-markers").assert_outcomes(passed=2)


def test_no_ad3_skips_marked_tests_and_fixture(pytester: pytest.Pytester):
    pytester.makepyfile(TESTS)
    pytester.runpytest("--fake", "--no-ad3", "--strict-markers").assert_outcomes(skipped=2)


def test_without_device_skips(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DWF_LIBRARY", str(pytester.path / "missing-dwf-library"))
    pytester.makepyfile(TESTS)
    result = pytester.runpytest("-rs")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*Analog Discovery 3 unavailable*"])


def test_settings_override(pytester: pytest.Pytester):
    pytester.makeconftest(
        """
import pytest

from ad3_waveforms_bench.pytest_plugin import Ad3Settings


@pytest.fixture(scope="session")
def ad3_settings():
    return Ad3Settings(analog_limits=(0.0, 5.0), vplus=3.3)
"""
    )
    pytester.makepyfile(
        """
def test_settings(ad3):
    assert ad3.analog_limits == (0.0, 5.0)
    assert ad3.api.calls_to("FDwfAnalogIOEnableSet")[-1][1] == 1
"""
    )
    pytester.runpytest("--fake").assert_outcomes(passed=1)


def test_serial_option_selects_device(pytester: pytest.Pytester):
    pytester.makepyfile(
        """
def test_serial(ad3):
    assert ad3.serial == "210415ABCDEF"
"""
    )
    pytester.runpytest("--fake", "--ad3-serial", "210415ABCDEF").assert_outcomes(passed=1)
    pytester.runpytest("--fake", "--ad3-serial", "nope").assert_outcomes(skipped=1)
