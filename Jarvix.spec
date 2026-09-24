# Build with: python -m PyInstaller --noconfirm Jarvix.spec
from pathlib import Path
import os
import sys
import sysconfig

root = Path(SPECPATH)
debug_console = os.environ.get("JARVIX_BUILD_CONSOLE") == "1"
app_name = "JarvixDebug" if debug_console else "Jarvix"
if sys.platform == "win32":
    # Ambient developer runtimes can contain incompatible DLLs with system names
    # (for example Poppler's versioned ICU). Resolve dependencies only from this
    # Python/Qt runtime and Windows. This changes the build process, not the OS.
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    packages = Path(sysconfig.get_paths()["purelib"])
    os.environ["PATH"] = os.pathsep.join(map(str, [
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        packages / "PySide6", packages / "shiboken6", windows / "System32", windows,
    ]))
a = Analysis(
    [str(root / "src" / "jarvix" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "LICENSE"), "."), (str(root / "src" / "jarvix" / "assets"), "jarvix/assets")],
    hiddenimports=["keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app_name, debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=debug_console,
          icon=str(root / "src" / "jarvix" / "assets" / "jarvix.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=app_name)
