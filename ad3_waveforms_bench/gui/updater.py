"""UpdateChecker: polls GitHub Releases for newer versions of ad3-waveforms-bench.

Runs in a daemon thread so it never blocks the Qt event loop. Emits Qt signals to hand results back to
the main thread.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
import webbrowser

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import __version__

logger = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/embedded-pro/ad3-waveforms-bench/releases/latest"
_REQUEST_TIMEOUT = 10


def parse_version(version: str) -> tuple[int, ...]:
    """`v1.2.3` or `1.2.3.dev4+gabc` -> (1, 2, 3); (0,) when there is no leading number."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0,)


def is_newer(latest: str, current: str) -> bool:
    """Whether release `latest` is newer than the running `current` version (a `.dev` build is older)."""
    latest_key, current_key = parse_version(latest), parse_version(current)
    if latest_key != current_key:
        return latest_key > current_key
    return ".dev" in current


class UpdateChecker(QObject):
    """Checks GitHub Releases in a background thread and signals the result.

    Signals
    -------
    update_available(latest_version, release_url):
        Emitted when a newer release is found.
    check_done:
        Emitted after every check, so callers can re-enable the controls disabled during the check.
    """

    update_available = Signal(str, str)
    check_done = Signal()

    def check_in_background(self) -> None:
        threading.Thread(target=self._check, daemon=True, name="update-check").start()

    def _check(self) -> None:
        try:
            request = urllib.request.Request(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": f"ad3-waveforms-bench/{__version__}"},
            )
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
                data: dict[str, object] = json.loads(response.read())
            tag = str(data.get("tag_name", "")).lstrip("v")
            html_url = str(data.get("html_url", ""))
            if not tag or not html_url:
                return
            if is_newer(tag, __version__):
                logger.info("Update available: v%s (current: v%s)", tag, __version__)
                self.update_available.emit(tag, html_url)
            else:
                logger.debug("No update found (latest: v%s)", tag)
        except urllib.error.URLError as exc:
            logger.debug("Update check network error: %s", exc)
        except Exception as exc:
            logger.debug("Update check failed: %s", exc)
        finally:
            self.check_done.emit()


class UpdateDialog(QDialog):
    """Asks the user whether to open the release page for the new version."""

    def __init__(self, latest_version: str, release_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._release_url = release_url
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>ad3-waveforms-bench {latest_version}</b> is available."))
        layout.addWidget(QLabel(f"You are running version {__version__}."))
        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("Open the release page to download the update?"))

        buttons = QDialogButtonBox()
        update_button = QPushButton("Open Release Page")
        later_button = QPushButton("Later")
        buttons.addButton(update_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(later_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._open_release_page)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_release_page(self) -> None:
        webbrowser.open(self._release_url)
        self.accept()
