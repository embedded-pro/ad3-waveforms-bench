"""SystemTrayIcon: keeps ad3-bench-server accessible from the notification area."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

if TYPE_CHECKING:
    from .updater import UpdateChecker

ICON_COLOR = "#E65100"
ICON_TEXT = "AD3"


def render_icon(size: int) -> QPixmap:
    """The application icon at `size` pixels: a rounded square with the `AD3` label."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(2, size // 16)
    radius = max(4, size // 5)
    painter.setBrush(QColor(ICON_COLOR))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, radius, radius)
    painter.setFont(QFont("Arial", max(5, int(size * 0.24)), QFont.Weight.Bold))
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ICON_TEXT)
    painter.end()
    return pixmap


def create_app_icon() -> QIcon:
    """Create the application icon programmatically; no external file is needed."""
    return QIcon(render_icon(64))


class SystemTrayIcon(QSystemTrayIcon):
    """System-tray presence for ad3-bench-server.

    Single-click or double-click toggles the main window. Right-click shows a context menu with
    Show/Hide, Start/Stop, Check for Updates, Open Log File and Quit.
    """

    def __init__(self, window: QWidget, parent: QObject | None = None) -> None:
        super().__init__(create_app_icon(), parent)
        self._window = window
        self._checker: UpdateChecker | None = None

        menu = QMenu()
        self._toggle_action = menu.addAction("Hide")
        self._toggle_action.triggered.connect(self._toggle_window)
        self.server_action = menu.addAction("Start Server")
        menu.addSeparator()
        self.check_updates_action = menu.addAction("Check for Updates")
        self.check_updates_action.triggered.connect(self._check_for_updates)
        log_action = menu.addAction("Open Log File")
        log_action.triggered.connect(self._open_log)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.quit)
        self._menu = menu
        self.setContextMenu(menu)
        self.setToolTip("ad3-bench-server")
        self.activated.connect(self._on_activated)

    def set_checker(self, checker: UpdateChecker) -> None:
        self._checker = checker

    def _check_for_updates(self) -> None:
        if self._checker is not None:
            self.check_updates_action.setEnabled(False)
            self._checker.check_in_background()

    def _open_log(self) -> None:
        from .log_file import open_log_file

        open_log_file()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window()

    def _toggle_window(self) -> None:
        if self._window.isVisible():
            self._window.hide()
            self._toggle_action.setText("Show")
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            self._toggle_action.setText("Hide")

    def set_running(self, running: bool, detail: str = "") -> None:
        self.server_action.setText("Stop Server" if running else "Start Server")
        self.setToolTip(f"ad3-bench-server - {detail}" if detail else "ad3-bench-server")

    def notify_server_started(self, address: str) -> None:
        self.showMessage("ad3-bench-server", f"Serving the AD3 on {address}.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def notify_server_stopped(self) -> None:
        self.showMessage("ad3-bench-server", "Server stopped.", QSystemTrayIcon.MessageIcon.Information, 2000)
