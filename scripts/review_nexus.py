"""Render both interfaces using disposable, offline local profiles."""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication

from jarvix.services import Services
from jarvix.ui.interface import create_window
from jarvix.ui.window import MainWindow
from review_ui import ReviewVault, settle


PAGES = ("Home", "Chat", "Operator", "Missions", "Knowledge", "Files",
         "Automations", "Integrations", "System", "Settings")


def seed(services, profile):
    folder = profile / "Jarvix"
    folder.mkdir()
    (folder / "release-checklist.md").write_text("# Release review\nVerify artifacts and permissions.\n", encoding="utf-8")
    (folder / "browser_bridge.py").write_text("def reconnect(peer):\n    return peer.connect()\n", encoding="utf-8")
    services.add_file_root(str(folder))
    services.scan_files()
    project = services.add_project("Jarvix", str(folder))
    task = services.add_task("Verify Windows package", (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat())
    services.add_task("Review browser connection errors")
    note = services.save_note("Browser reconnect checklist", "Retry a verified read. Never replay a pending write.")
    space = services.knowledge_spaces.create("Jarvix release", project_id=project)
    services.knowledge_spaces.add_source(space["id"], "note", note)
    services.missions.save("Prepare Jarvix for release", project_id=project, task_ids=[task], note_ids=[note],
        blockers=["Package verification is pending"], next_steps=["Run the package startup check"],
        milestones=[{"id": "review", "title": "Review interface", "status": "complete"},
                    {"id": "package", "title": "Verify executable", "status": "pending"}])
    services.workflows.save("Review today's work", [{"kind": "action", "tool": "tasks.list", "arguments": {}}],
                           kind="routine", enabled=False)
    services.operator.run({"goal": "Inspect unfinished tasks", "steps": [
        {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    conversation = services.new_conversation("Browser connection review")
    services.repository.append_message(conversation, "user", "How should I verify browser reconnect?")
    services.repository.append_message(conversation, "assistant", "Check a fresh read. Pending writes need review.\n\n"
        "```python\nassert peer.connected\nassert pending_write.status == 'awaiting_review'\n```\n\n"
        "| Check | Expected result |\n| --- | --- |\n| Read | Completes |\n| Pending write | Requires review |")
    return conversation


def capture(app, profile, mode, output, size, conversation, snapshot, *, compare=False):
    services = Services(profile, vault=ReviewVault())
    services.settings.set("ui.interface", mode)
    with patch.object(MainWindow, "refresh_system", lambda self: None), patch.object(MainWindow, "run_routines", lambda self: None):
        window = create_window(services)
        window.snapshot = snapshot
        window.resize(*size)
        window.show()
        try:
            for name in (("Chat",) if compare else PAGES):
                window.navigate(name)
                if name == "Chat" and conversation:
                    window.pages[name].load_conversation(conversation)
                settle(app, window)
                # Allow scroll containers to finish their deferred size updates.
                settle(app, window)
                # A direct call makes controller failures fail the review process;
                # Python exceptions in asynchronous Qt slots can otherwise be printed only.
                window.pages[name].refresh()
                settle(app, window)
                if compare:
                    window.pages["Chat"].composer.setFocus()
                    window.clock_label.setText("Interface comparison")
                    app.processEvents()
                image = window.grab().toImage()
                if not image.save(str(output / (name.lower() + ".png"))):
                    raise OSError("Could not save " + name)
                print(f"{mode} {name}: {window.width()}x{window.height()}", flush=True)
                if name == "Settings" and not compare:
                    settings = window.pages[name]
                    appearance = next(i for i in range(settings.categories.count())
                                      if settings.categories.item(i).text() == "Appearance")
                    settings.categories.setCurrentRow(appearance)
                    settle(app, window)
                    if not window.grab().save(str(output / "settings-appearance.png")):
                        raise OSError("Could not save Appearance settings")
            return image
        finally:
            window.close()
            settle(app, window)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            services.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    parser.add_argument("--mode", choices=("legacy", "nexus"), default="nexus")
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--matrix", action="store_true", help="Render laptop, desktop and 4K sizes at four display scales.")
    args = parser.parse_args()
    if args.matrix:
        for width, height in ((860, 600), (1280, 840), (1920, 1080)):
            for scale in ("1", "1.25", "1.5", "2"):
                command = [sys.executable, __file__, "--width", str(width), "--height", str(height), "--mode", args.mode]
                if args.empty:
                    command.append("--empty")
                subprocess.run(command, env={**os.environ, "QT_SCALE_FACTOR": scale}, check=True)
        command = [sys.executable, __file__, "--width", "2560", "--height", "1440", "--mode", args.mode]
        if args.empty:
            command.append("--empty")
        subprocess.run(command, env={**os.environ, "QT_SCALE_FACTOR": "1"}, check=True)
        return
    output = Path("artifacts/nexus") / (f"{args.width}x{args.height}-scale{os.environ.get('QT_SCALE_FACTOR', '1')}-{args.mode}"
                                       + ("-empty" if args.empty else ""))
    output.mkdir(parents=True, exist_ok=True)
    profile = output / ("profile-" + uuid.uuid4().hex)
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI Variable", 10))
    app.setCursorFlashTime(0)
    app.setQuitOnLastWindowClosed(False)
    services = Services(profile, vault=ReviewVault())
    conversation = None if args.empty else seed(services, profile)
    snapshot = services.system_snapshot()
    services.close()
    before_path, after_path = output / "legacy-before", output / "legacy-after"
    for path in (before_path, after_path):
        path.mkdir(exist_ok=True)
    before = capture(app, profile, "legacy", before_path, (args.width, args.height), conversation, snapshot, compare=True)
    capture(app, profile, args.mode, output, (args.width, args.height), conversation, snapshot)
    after = capture(app, profile, "legacy", after_path, (args.width, args.height), conversation, snapshot, compare=True)
    before_pixels = before.convertToFormat(QImage.Format.Format_RGBA8888)
    after_pixels = after.convertToFormat(QImage.Format.Format_RGBA8888)
    if before.size() != after.size() or before_pixels.constBits().tobytes() != after_pixels.constBits().tobytes():
        raise AssertionError("Legacy presentation changed after reopening from the selected interface.")
    print("Legacy before/after images match exactly.", flush=True)


if __name__ == "__main__":
    main()
