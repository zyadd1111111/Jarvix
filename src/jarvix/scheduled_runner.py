"""Limited scheduled entry point. No shell, arbitrary tool name, or approval override."""
from __future__ import annotations

import re
from pathlib import Path


def run_scheduled(data_dir, schedule_id):
    if data_dir is None or not re.fullmatch(r"[a-f0-9-]{32,36}", schedule_id):
        return 2
    from PySide6.QtCore import QCoreApplication, QLockFile
    from jarvix.services import Services
    # Hold before Services constructs session services and marks interrupted runs.
    app = QCoreApplication.instance() or QCoreApplication([])
    profile = Path(data_dir).resolve(strict=True)
    lock = QLockFile(str(profile / "jarvix.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        directory = profile / "schedule-inbox"
        directory.mkdir(exist_ok=True)
        if directory.is_symlink():
            return 2
        try:
            with (directory / (schedule_id + ".request")).open("x", encoding="ascii") as request:
                request.write(schedule_id)
        except FileExistsError:
            pass
        return 0
    services = None
    try:
        services = Services(profile)
        result = services.scheduler.run(schedule_id)
        app.processEvents()
        return 0 if result.get("ok") or result.get("skipped") else 3
    except Exception:
        if services:
            services.repository.audit("schedule", "Closed-app execution blocked or failed; inspect approved schedule")
            services.notifications.create("Background schedule needs attention",
                "Execution was blocked or failed. Review the approved schedule and workflow history.", "schedule", "high")
        return 2
    finally:
        if services:
            services.close()
        lock.unlock()

