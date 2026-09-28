import os
import socket
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ad3_waveforms_bench import AnalogDiscovery3  # noqa: E402
from ad3_waveforms_bench.gui.main_window import MainWindow  # noqa: E402
from ad3_waveforms_bench.gui.tray import render_icon  # noqa: E402
from ad3_waveforms_bench.gui.updater import is_newer, parse_version  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(app, settings):
    main_window = MainWindow(settings=settings)
    yield main_window
    main_window.shutdown()
    main_window.deleteLater()


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _spin(app, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        app.processEvents()
        time.sleep(0.01)


def test_start_serve_and_stop(app, window):
    port = _free_port()
    window._fake_check.setChecked(True)
    window._port_edit.setText(str(port))
    window.toggle_server()
    _spin(app, lambda: window._start_button.text() == "Stop")
    assert not window._port_edit.isEnabled()

    device = AnalogDiscovery3(remote=f"127.0.0.1:{port}")
    device.open()
    _spin(app, lambda: window._client_label.text().startswith("Client 127.0.0.1:"))
    device.dio.drive(1, 1)
    assert device.dio.read(1) == 1
    device.close()
    _spin(app, lambda: window._client_label.text() == "Client (none)")

    window.toggle_server()
    _spin(app, lambda: window._start_button.text() == "Start" and window._start_button.isEnabled())
    assert window._port_edit.isEnabled()
    log = window._log_view.toPlainText()
    assert f"Serving the AD3 on 127.0.0.1:{port}" in log
    assert "connected" in log


def test_port_in_use_is_reported(app, window):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        window._fake_check.setChecked(True)
        window._port_edit.setText(str(busy.getsockname()[1]))
        window.toggle_server()
        _spin(app, lambda: "Cannot start" in window._log_view.toPlainText())
        _spin(app, lambda: window._start_button.isEnabled())
    assert window._start_button.text() == "Start"
    assert not window.controller.is_running


def test_missing_library_is_reported(app, window, tmp_path):
    window._library_edit.setText(str(tmp_path / "missing-dwf-library"))
    window.toggle_server()
    _spin(app, lambda: "Cannot start" in window._log_view.toPlainText())
    _spin(app, lambda: window._start_button.isEnabled())


def test_detect_devices_with_fake(window):
    window._fake_check.setChecked(True)
    window._detect_devices()
    log = window._log_view.toPlainText()
    assert "Analog Discovery 3" in log
    assert "WaveForms runtime fake" in log


def test_settings_are_remembered(app, settings):
    first = MainWindow(settings=settings)
    first._port_edit.setText("6000")
    first._token_edit.setText("s3cret")
    first._fake_check.setChecked(True)
    first._autostart_check.setChecked(True)
    first.shutdown()
    second = MainWindow(settings=settings)
    config = second.build_config()
    assert (config.port, config.token, config.fake) == (6000, "s3cret", True)
    assert second.start_on_launch
    assert "host.docker.internal:6000" in second._client_hint.text()


def test_version_comparison():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.2.1.dev3+gabc") == (0, 2, 1)
    assert parse_version("garbage") == (0,)
    assert is_newer("0.3.0", "0.2.9")
    assert not is_newer("0.2.0", "0.2.0")
    assert is_newer("0.2.1", "0.2.1.dev3+gabc")
    assert not is_newer("0.2.0", "0.2.1.dev3+gabc")


def test_icon(app):
    assert render_icon(64).width() == 64


def test_cli_help(capsys):
    from ad3_waveforms_bench.gui.__main__ import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "--minimized" in capsys.readouterr().out


def test_log_file_setup(monkeypatch, tmp_path):
    import logging
    import sys

    from ad3_waveforms_bench.gui import log_file

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(log_file.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    try:
        path = log_file.setup()
        assert path.parent.name == "ad3-bench-server"
        assert tmp_path in path.parents
        logging.getLogger("ad3_waveforms_bench.test").warning("written to the file")
        for handler in root.handlers:
            handler.flush()
        assert "written to the file" in path.read_text()
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(("tag", "expected"), [("v999.0.0", ["999.0.0"]), ("v0.0.0", [])])
def test_update_checker(app, monkeypatch, tag, expected):
    from ad3_waveforms_bench.gui import updater

    monkeypatch.setattr(updater, "__version__", "0.1.0")
    url = "https://github.com/embedded-pro/ad3-waveforms-bench/releases/tag/" + tag
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda request, timeout: _Response({"tag_name": tag, "html_url": url}))
    checker = updater.UpdateChecker()
    found, done = [], []
    checker.update_available.connect(lambda version, link: found.append(version))
    checker.check_done.connect(lambda: done.append(True))
    checker._check()
    assert found == expected
    assert done == [True]


def test_update_dialog(app, monkeypatch):
    from ad3_waveforms_bench.gui import updater

    opened = []
    monkeypatch.setattr(updater.webbrowser, "open", opened.append)
    dialog = updater.UpdateDialog("9.9.9", "https://example.invalid/release")
    dialog._open_release_page()
    assert opened == ["https://example.invalid/release"]


def test_tray_follows_the_server(app, window):
    from ad3_waveforms_bench.gui.tray import SystemTrayIcon

    tray = SystemTrayIcon(window)
    window.set_tray(tray)
    tray.set_running(True, "127.0.0.1:5025")
    assert tray.server_action.text() == "Stop Server"
    assert "127.0.0.1:5025" in tray.toolTip()
    tray.set_running(False)
    assert tray.server_action.text() == "Start Server"
    window.show()
    tray._toggle_window()
    assert not window.isVisible()
    tray._toggle_window()
    assert window.isVisible()


def test_main_runs_and_quits(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer

    from ad3_waveforms_bench.gui import log_file
    from ad3_waveforms_bench.gui.__main__ import main

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(log_file, "setup", lambda: tmp_path / "gui.log")
    QTimer.singleShot(200, app.quit)
    with pytest.raises(SystemExit) as exit_info:
        main(["--minimized"])
    assert exit_info.value.code == 0
