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
    parser.add_argument("--scheduled-run", help="Execute one previously approved Windows schedule")
    args = parser.parse_args()
    if args.scheduled_run:
        from jarvix.scheduled_runner import run_scheduled
        return run_scheduled(args.data_dir, args.scheduled_run)
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox
    from jarvix.services import Services
    from jarvix.ui.interface import create_window

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Jarvix")
    app.setOrganizationName("Jarvix")
    app.setFont(QFont("Segoe UI Variable", 10))
    from platformdirs import user_data_path
    data_dir = Path(args.data_dir or os.environ.get("JARVIX_DATA_DIR") or user_data_path("Jarvix", appauthor=False))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "jarvix.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "Jarvix is running", "This Jarvix profile is already open. Use --data-dir for another profile.")
        return 1
    try:
        services = Services(data_dir)
    except Exception:
        lock.unlock()
        if not args.screenshot:
            QMessageBox.critical(None, "Jarvix profile needs attention",
                "This profile could not be opened. Its data has been preserved. Check the profile permissions, Windows account and local backup before trying again.")
        return 2
    window = create_window(services)
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
