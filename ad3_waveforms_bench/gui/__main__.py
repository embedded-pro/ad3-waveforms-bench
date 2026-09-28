"""GUI entry point: `python -m ad3_waveforms_bench.gui` or `ad3-bench-gui`.

`--start` starts the server right away (as does the "Start the server on launch" setting) and
`--minimized` starts in the system tray; the Windows installer's autostart shortcut passes both.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ad3-bench-gui", description="Share this machine's Analog Discovery 3 over TCP (GUI).")
    parser.add_argument("--start", action="store_true", help="start the server right away")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    except ImportError:
        print('PySide6 is required for the GUI. Install it with:\n    pip install "ad3-waveforms-bench[gui]"', file=sys.stderr)
        sys.exit(1)

    # Absolute imports: PyInstaller runs this file as a top-level script.
    # File logging comes first so even early crashes are captured on disk.
    from ad3_waveforms_bench.gui import log_file
    from ad3_waveforms_bench.gui.main_window import MainWindow
    from ad3_waveforms_bench.gui.tray import SystemTrayIcon, create_app_icon
    from ad3_waveforms_bench.gui.updater import UpdateChecker, UpdateDialog

    log_path = log_file.setup()

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("ad3-bench-server")
    app.setOrganizationName("embedded-pro")
    app.setWindowIcon(create_app_icon())
    # Don't quit when the last window is hidden: the server keeps running from the tray.
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(log_path=log_path)
    app.aboutToQuit.connect(window.shutdown)

    tray: SystemTrayIcon | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = SystemTrayIcon(window, app)
        tray.show()
        window.set_tray(tray)
    else:
        app.setQuitOnLastWindowClosed(True)

    if not (args.minimized and tray is not None):
        window.show()

    # Update checker: fires 3 s after startup and on demand.
    checker = UpdateChecker(app)

    def _on_update_available(version: str, url: str) -> None:
        _on_check_done()
        UpdateDialog(version, url, window).exec()

    def _on_check_done() -> None:
        window.restore_check_button()
        if tray is not None:
            tray.check_updates_action.setEnabled(True)

    checker.update_available.connect(_on_update_available)
    checker.check_done.connect(_on_check_done)
    window.set_checker(checker)
    if tray is not None:
        tray.set_checker(checker)
    QTimer.singleShot(3000, checker.check_in_background)

    if args.start or window.start_on_launch:
        QTimer.singleShot(0, window.toggle_server)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
