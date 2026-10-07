"""Measure real Qt navigation, resize, idle cost and the native window backdrop."""
import argparse
import json
from pathlib import Path
import time
import uuid
from unittest.mock import patch

import psutil
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from jarvix.services import Services
from jarvix.ui.interface import create_window
from jarvix.ui.window import MainWindow
from review_ui import ReviewVault, settle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default="offscreen", choices=("offscreen", "windows"))
    args = parser.parse_args()
    app = QApplication(["Jarvix material review", "-platform", args.platform])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI Variable", 10))
    output = Path("artifacts/nexus")
    output.mkdir(parents=True, exist_ok=True)
    services = Services(output / ("performance-" + uuid.uuid4().hex), vault=ReviewVault())
    services.settings.set("ui.interface", "nexus")
    timings = []
    start = time.perf_counter()
    with patch.object(MainWindow, "refresh_system", lambda self: None), patch.object(MainWindow, "run_routines", lambda self: None):
        window = create_window(services)
        window.resize(1280, 840)
        window.show()
        try:
            settle(app, window)
            startup = (time.perf_counter() - start) * 1000
            for name in ("Chat", "Operator", "Missions", "Knowledge", "Files", "Settings", "Home"):
                begin = time.perf_counter()
                window.navigate(name)
                settle(app, window)
                timings.append({"page": name, "first_navigation_ms": round((time.perf_counter() - begin) * 1000, 2)})
            services.settings.set("nexus.motion", False)
            window.configure_materials()
            available = window.screen().availableGeometry()
            frame_width = window.frameGeometry().width() - window.width()
            frame_height = window.frameGeometry().height() - window.height()
            window.move(available.topLeft())
            begin = time.perf_counter()
            for width, height in ((860, 600), (1024, 700), (1280, 840), (1920, 1080)) * 5:
                window.resize(min(width, available.width() - frame_width), min(height, available.height() - frame_height))
                app.processEvents()
                window.grab()
            resize_ms = (time.perf_counter() - begin) * 1000 / 20
            window.resize(1280, 840)
            settle(app, window)
            process = psutil.Process()
            cpu_start = sum(process.cpu_times()[:2])
            idle_start = time.perf_counter()
            QTest.qWait(2000)
            idle_cpu = (sum(process.cpu_times()[:2]) - cpu_start) / (time.perf_counter() - idle_start) * 100
            screens = [{"name": screen.name(), "width": screen.size().width(), "height": screen.size().height(),
                        "scale": screen.devicePixelRatio()} for screen in QGuiApplication.screens()]
            report = {"platform": QGuiApplication.platformName(), "composition": window.composition_status,
                "startup_ms": round(startup, 2), "navigation": timings, "average_resize_capture_ms": round(resize_ms, 2),
                "idle_cpu_percent_one_core": round(idle_cpu, 2), "rss_mb": round(process.memory_info().rss / 1024**2, 1),
                "screens": screens, "notes": "Offline isolated profile; first navigation includes existing lazy work and settling. No cloud requests."}
            (output / ("performance-" + args.platform + ".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
            window.grab().save(str(output / ("native-" + args.platform + ".png")))
            print(json.dumps(report, indent=2), flush=True)
        finally:
            window.close()
            settle(app, window)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            services.close()


if __name__ == "__main__":
    main()
