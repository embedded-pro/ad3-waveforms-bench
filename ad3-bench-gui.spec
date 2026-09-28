# PyInstaller spec for ad3-bench-gui.
#
# Windows (onefile .exe, consumed by Inno Setup):
#   pyinstaller ad3-bench-gui.spec
#
# Linux (onedir, consumed by appimagetool):
#   pyinstaller ad3-bench-gui.spec
#
# The spec detects the platform and switches mode automatically. Run scripts/generate_icon.py first.

import sys

from PyInstaller.utils.hooks import copy_metadata

_WINDOWS = sys.platform == "win32"
_ICON = "assets/icon.ico" if _WINDOWS else "assets/icon.png"

a = Analysis(
    ["ad3_waveforms_bench/gui/__main__.py"],
    pathex=[],
    binaries=[],
    # importlib.metadata reads the version (About, update check) from the dist-info.
    datas=copy_metadata("ad3-waveforms-bench"),
    hiddenimports=[
        "ad3_waveforms_bench.gui.main_window",
        "ad3_waveforms_bench.gui.server_controller",
        "ad3_waveforms_bench.gui.tray",
        "ad3_waveforms_bench.gui.updater",
        "ad3_waveforms_bench.gui.log_file",
        # Loaded lazily by the controller.
        "ad3_waveforms_bench.instruments.dwf",
        "ad3_waveforms_bench.instruments.fake",
        "ad3_waveforms_bench.remote.server",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The GUI never runs tests; keep pytest (a runtime dependency of the plugin) out of the bundle.
    excludes=["pytest", "_pytest", "ad3_waveforms_bench.pytest_plugin"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if _WINDOWS:
    # Single .exe; Inno Setup wraps it into the installer.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ad3-bench-gui",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        icon=_ICON,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    # One-directory bundle; appimagetool wraps it into an AppImage.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ad3-bench-gui",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        icon=_ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="ad3-bench-gui",
    )
