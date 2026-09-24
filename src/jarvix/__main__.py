"""Desktop entry point and deterministic visual smoke capture."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvix local-first desktop assistant")
    parser.add_argument("--data-dir", type=Path, help="Use a separate local profile")
    parser.add_argument("--screenshot", type=Path, help="Render an offscreen screenshot, then exit")
    parser.add_argument("--page", help="Override the saved workspace section")
    args = parser.parse_args()
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox
    from jarvix.services import Services
    from jarvix.ui import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Jarvix")
    app.setOrganizationName("Jarvix")
    app.setFont(QFont("Segoe UI", 10))
    services = Services(args.data_dir)
    lock = QLockFile(str(services.data_dir / "jarvix.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "Jarvix is running", "This Jarvix profile is already open. Use --data-dir for another profile.")
        services.close()
        return 1
    window = MainWindow(services)
    if args.screenshot:
        window.resize(1440, 940)
    if args.page:
        window.navigate(args.page)
    window.show()
    screenshot_status = [0]
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            success = window.grab().save(str(args.screenshot))
            screenshot_status[0] = 0 if success else 2
            window.close()  # Drain owned workers through the normal close path.
        QTimer.singleShot(2000, capture)
    try:
        result = app.exec()
        return screenshot_status[0] if args.screenshot else result
    finally:
        services.close()
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
