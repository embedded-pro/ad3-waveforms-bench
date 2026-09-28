"""MainWindow: the ad3-bench-server GUI main window.

Layout
------
┌─────────────────────────────────────────────────────────┐
│  WaveForms ───────────────────────────────────────────  │
│  Library [_______________________] [Browse] [Detect]    │
│  [ ] Fake device (no hardware)                          │
│  Server ──────────────────────────────────────────────  │
│  Bind address [__________]  TCP port [_____]            │
│  Token [______________]                                 │
│  Clients: AD3_REMOTE=host.docker.internal:5025          │
│  General ─────────────────────────────────────────────  │
│  Log level [_____▼]  [ ] Start the server on launch     │
│                                                         │
│  ● Server  ● Client (none)             [ Start ]        │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  log output                                       │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, Qt, Slot
from PySide6.QtGui import QCloseEvent, QColor, QIntValidator, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..remote.wire import DEFAULT_PORT
from .server_controller import ServerConfig, ServerController, backend_factory

if TYPE_CHECKING:
    from .tray import SystemTrayIcon
    from .updater import UpdateChecker

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "#888888",
    logging.INFO: "#dddddd",
    logging.WARNING: "#f0c060",
    logging.ERROR: "#ff6060",
    logging.CRITICAL: "#ff6060",
}

_IDLE = "#444444"
_ACTIVE = "#44dd44"
_FAILED = "#ff4444"


def _status_dot(color: str) -> QLabel:
    label = QLabel("●")
    _set_dot(label, color)
    return label


def _set_dot(label: QLabel, color: str) -> None:
    label.setStyleSheet(f"color: {color}; font-size: 18px;")


class MainWindow(QMainWindow):
    def __init__(self, log_path: Path | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("ad3-bench-server")
        self.setMinimumWidth(600)

        self._tray: SystemTrayIcon | None = None
        self._log_path = log_path
        self._checker: UpdateChecker | None = None
        self._settings = settings if settings is not None else QSettings()

        self._controller = ServerController(self)
        self._controller.started.connect(self._on_started)
        self._controller.stopped.connect(self._on_stopped)
        self._controller.error.connect(self._on_error)
        self._controller.log_record.connect(self._on_log_record)
        self._controller.client_changed.connect(self._on_client_changed)

        self._server_dot = _status_dot(_IDLE)
        self._client_dot = _status_dot(_IDLE)
        self._client_label = QLabel("Client (none)")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.addWidget(self._build_waveforms_group())
        layout.addWidget(self._build_server_group())
        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_status_row())
        layout.addWidget(self._build_log_panel(), stretch=1)

        self._setup_status_bar()
        self._load_settings()
        self._update_client_hint()

    @property
    def controller(self) -> ServerController:
        return self._controller

    @property
    def start_on_launch(self) -> bool:
        return self._autostart_check.isChecked()

    def set_tray(self, tray: SystemTrayIcon | None) -> None:
        self._tray = tray
        if tray is not None:
            tray.server_action.triggered.connect(self.toggle_server)

    def set_checker(self, checker: UpdateChecker) -> None:
        self._checker = checker

    def closeEvent(self, event: QCloseEvent) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable() and self._tray is not None:
            self.hide()
            event.ignore()
        else:
            self.shutdown()
            event.accept()

    def shutdown(self) -> None:
        """Stop the server (returning any open device to the safe state) and save the settings."""
        self._save_settings()
        if self._controller.is_running:
            self._controller.stop()
            self._controller.wait(5.0)

    # ------------------------------------------------------------------
    # Builder helpers
    # ------------------------------------------------------------------

    def _build_waveforms_group(self) -> QGroupBox:
        box = QGroupBox("WaveForms")
        form = QFormLayout(box)

        self._library_edit = QLineEdit()
        self._library_edit.setPlaceholderText("default (dwf.dll / libdwf.so / dwf.framework)")
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._browse_library)
        detect_button = QPushButton("Detect devices")
        detect_button.clicked.connect(self._detect_devices)
        row = QHBoxLayout()
        row.addWidget(self._library_edit, stretch=1)
        row.addWidget(browse_button)
        row.addWidget(detect_button)
        form.addRow("Library:", row)

        self._fake_check = QCheckBox("Fake device (no hardware, for trying the setup)")
        form.addRow("", self._fake_check)
        return box

    def _build_server_group(self) -> QGroupBox:
        box = QGroupBox("Server")
        form = QFormLayout(box)

        self._bind_edit = QLineEdit("127.0.0.1")
        self._port_edit = QLineEdit(str(DEFAULT_PORT))
        self._port_edit.setValidator(QIntValidator(1, 65535, self))
        self._port_edit.setMaximumWidth(80)
        self._port_edit.textChanged.connect(self._update_client_hint)
        row = QHBoxLayout()
        row.addWidget(self._bind_edit, stretch=1)
        row.addWidget(QLabel("TCP port"))
        row.addWidget(self._port_edit)
        form.addRow("Bind address:", row)

        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("optional shared secret (AD3_REMOTE_TOKEN on the client)")
        self._token_edit.textChanged.connect(self._update_client_hint)
        form.addRow("Token:", self._token_edit)

        self._client_hint = QLabel()
        self._client_hint.setStyleSheet("color: #888888;")
        self._client_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Clients:", self._client_hint)
        return box

    def _build_general_group(self) -> QGroupBox:
        box = QGroupBox("General")
        row = QHBoxLayout(box)
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(LOG_LEVELS)
        self._log_level_combo.setCurrentText("INFO")
        self._autostart_check = QCheckBox("Start the server on launch")
        row.addWidget(QLabel("Log level:"))
        row.addWidget(self._log_level_combo)
        row.addSpacing(16)
        row.addWidget(self._autostart_check)
        row.addStretch()
        return box

    def _build_status_row(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._server_dot)
        row.addWidget(QLabel("Server"))
        row.addSpacing(16)
        row.addWidget(self._client_dot)
        row.addWidget(self._client_label)
        row.addStretch()
        self._start_button = QPushButton("Start")
        self._start_button.setMinimumWidth(100)
        self._start_button.clicked.connect(self.toggle_server)
        row.addWidget(self._start_button)
        return widget

    def _build_log_panel(self) -> QPlainTextEdit:
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setStyleSheet("QPlainTextEdit { background: #1e1e1e; color: #dddddd; font-family: monospace; font-size: 11px; }")
        return self._log_view

    def _setup_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        if self._log_path is not None:
            log_label = QLabel(f"Log: {self._log_path}")
            log_label.setStyleSheet("color: #888888; font-size: 10px;")
            bar.addWidget(log_label, 1)
            open_button = QPushButton("Open Log")
            open_button.setFlat(True)
            open_button.setStyleSheet("color: #aaaaff; font-size: 10px;")
            open_button.clicked.connect(self._open_log)
            bar.addPermanentWidget(open_button)
        self._check_updates_button = QPushButton("Check for Updates")
        self._check_updates_button.setFlat(True)
        self._check_updates_button.setStyleSheet("color: #aaaaff; font-size: 10px;")
        self._check_updates_button.clicked.connect(self._check_for_updates)
        bar.addPermanentWidget(self._check_updates_button)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot()
    def toggle_server(self) -> None:
        self._start_button.setEnabled(False)
        if self._controller.is_running:
            self._controller.stop()
        else:
            self._save_settings()
            self._set_inputs_enabled(False)
            self._controller.start(self.build_config())

    @Slot()
    def _check_for_updates(self) -> None:
        if self._checker is not None:
            self._check_updates_button.setEnabled(False)
            self._check_updates_button.setText("Checking…")
            self._checker.check_in_background()

    @Slot()
    def restore_check_button(self) -> None:
        self._check_updates_button.setEnabled(True)
        self._check_updates_button.setText("Check for Updates")

    @Slot()
    def _open_log(self) -> None:
        from .log_file import open_log_file

        open_log_file()

    @Slot(str)
    def _on_started(self, address: str) -> None:
        self._start_button.setText("Stop")
        self._start_button.setEnabled(True)
        _set_dot(self._server_dot, _ACTIVE)
        self._append_log_line(f"[INFO] Serving the AD3 on {address}", "#aaddff")
        if self._tray is not None:
            self._tray.set_running(True, address)
            self._tray.notify_server_started(address)

    @Slot()
    def _on_stopped(self) -> None:
        was_running = self._start_button.text() == "Stop"
        self._start_button.setText("Start")
        self._start_button.setEnabled(True)
        self._set_inputs_enabled(True)
        if was_running:
            _set_dot(self._server_dot, _IDLE)
        self._on_client_changed("")
        if self._tray is not None:
            self._tray.set_running(False)
            if was_running:
                self._tray.notify_server_stopped()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        _set_dot(self._server_dot, _FAILED)
        self._append_log_line(f"[ERROR] {message}", "#ff6060")

    @Slot(object)
    def _on_log_record(self, record: logging.LogRecord) -> None:
        self._append_log_line(self._format_record(record), _LEVEL_COLORS.get(record.levelno, "#dddddd"))

    @Slot(str)
    def _on_client_changed(self, client: str) -> None:
        _set_dot(self._client_dot, _ACTIVE if client else _IDLE)
        self._client_label.setText(f"Client {client}" if client else "Client (none)")

    @Slot()
    def _browse_library(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "WaveForms library", "", "Libraries (*.dll *.so *.dylib dwf);;All files (*)")
        if path:
            self._library_edit.setText(path)

    @Slot()
    def _detect_devices(self) -> None:
        from ..instruments.ad3 import AnalogDiscovery3

        self._append_log_line("[INFO] Scanning for Digilent devices...", "#888888")
        try:
            api = backend_factory(self.build_config())()
            version = getattr(api, "version", None)
            if callable(version):
                self._append_log_line(f"  WaveForms runtime {version()}", "#aaddff")
            devices = AnalogDiscovery3.list_devices(api)
        except Exception as exc:
            self._append_log_line(f"[ERROR] Detection failed: {exc}", "#ff6060")
            return
        if not devices:
            self._append_log_line("[INFO] No Digilent device detected.", "#888888")
        for index, name, serial in devices:
            self._append_log_line(f"  #{index}  {name}  {serial}", "#aaddff")

    @Slot()
    def _update_client_hint(self) -> None:
        text = f"AD3_REMOTE=host.docker.internal:{self._port_edit.text() or DEFAULT_PORT}"
        if self._token_edit.text():
            text += "  AD3_REMOTE_TOKEN=…"
        self._client_hint.setText(text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def build_config(self) -> ServerConfig:
        return ServerConfig(
            port=int(self._port_edit.text() or DEFAULT_PORT),
            bind_address=self._bind_edit.text().strip() or "127.0.0.1",
            token=self._token_edit.text() or None,
            fake=self._fake_check.isChecked(),
            dwf_library=self._library_edit.text().strip() or None,
            log_level=self._log_level_combo.currentText(),
        )

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (self._library_edit, self._fake_check, self._bind_edit, self._port_edit, self._token_edit, self._log_level_combo):
            widget.setEnabled(enabled)

    def _load_settings(self) -> None:
        settings = self._settings
        self._library_edit.setText(str(settings.value("waveforms/library", "")))
        self._fake_check.setChecked(_as_bool(settings.value("waveforms/fake", False)))
        self._bind_edit.setText(str(settings.value("server/bind", "127.0.0.1")))
        self._port_edit.setText(str(settings.value("server/port", DEFAULT_PORT)))
        self._token_edit.setText(str(settings.value("server/token", "")))
        level = str(settings.value("general/log_level", "INFO"))
        self._log_level_combo.setCurrentText(level if level in LOG_LEVELS else "INFO")
        self._autostart_check.setChecked(_as_bool(settings.value("general/start_on_launch", False)))

    def _save_settings(self) -> None:
        settings = self._settings
        settings.setValue("waveforms/library", self._library_edit.text().strip())
        settings.setValue("waveforms/fake", self._fake_check.isChecked())
        settings.setValue("server/bind", self._bind_edit.text().strip())
        settings.setValue("server/port", self._port_edit.text())
        settings.setValue("server/token", self._token_edit.text())
        settings.setValue("general/log_level", self._log_level_combo.currentText())
        settings.setValue("general/start_on_launch", self._autostart_check.isChecked())
        settings.sync()

    def _append_log_line(self, text: str, color: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self._log_view.setTextCursor(cursor)
        self._log_view.ensureCursorVisible()

    @staticmethod
    def _format_record(record: logging.LogRecord) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"[{stamp}] [{record.levelname}] {record.name}: {record.getMessage()}"


def _as_bool(value: object) -> bool:
    """QSettings returns "true"/"false" strings from INI files and real bools from the registry."""
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes")
    return bool(value)
