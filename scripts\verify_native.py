"""Read-only Windows smoke check; prints no personal context."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication

from jarvix.services import Services


def main():
    application = QCoreApplication.instance() or QCoreApplication([])
    application.processEvents()
    failed = []
    with TemporaryDirectory(prefix="jarvix-native-check-") as directory:
        service = Services(Path(directory))
        try:
            for name in ("system.device", "system.uptime", "system.storage", "system.battery",
                         "system.network", "system.monitors", "system.gpu", "system.audio_devices",
                         "windows.list", "windows.foreground", "system.services", "apps.discover"):
                result = service.execute_tool(name, {})
                print(f"{name}: {'PASS' if result.ok else 'UNAVAILABLE'}")
                if not result.ok:
                    failed.append(name)
            for name in ("clipboard.read", "screen.capture"):
                result = service.execute_tool(name, {}, approve=lambda _: True)
                assert not result.ok, "An opt-in gate was bypassed"
            print("clipboard/screen opt-in gates: PASS (no capture performed)")
        finally:
            service.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
