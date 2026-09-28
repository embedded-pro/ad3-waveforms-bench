#!/usr/bin/env python3
"""Generate assets/icon.png and assets/icon.ico used by PyInstaller and the installers.

Run once before building:
    python scripts/generate_icon.py

Requires PySide6 (the `gui` extra); set QT_QPA_PLATFORM=offscreen without a display.
Produces:
    assets/icon.png   256x256 RGBA, used by the AppImage and the .desktop file
    assets/icon.ico   multi-size ICO, used by the Windows installer and .exe
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def _png_bytes(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice

    from ad3_waveforms_bench.gui.tray import render_icon

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    render_icon(size).save(buffer, "PNG")
    buffer.close()
    return bytes(buffer.data().data())


def build_ico(png_sizes: dict[int, bytes]) -> bytes:
    """Pack PNG blobs into an ICO file (PNG-compressed frames)."""
    header = struct.pack("<HHH", 0, 1, len(png_sizes))
    offset = 6 + 16 * len(png_sizes)
    entries = b""
    images = b""
    for size, png in sorted(png_sizes.items()):
        edge = 0 if size >= 256 else size  # 0 means 256 in ICO
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(png), offset + len(images))
        images += png
    return header + entries + images


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)

    png_256 = _png_bytes(256)
    (ASSETS / "icon.png").write_bytes(png_256)
    print(f"Written {ASSETS / 'icon.png'}  ({len(png_256)} bytes)")

    ico = build_ico({size: _png_bytes(size) for size in (16, 32, 48, 64, 128)} | {256: png_256})
    (ASSETS / "icon.ico").write_bytes(ico)
    print(f"Written {ASSETS / 'icon.ico'}  ({len(ico)} bytes)")
    _ = app


if __name__ == "__main__":
    main()
