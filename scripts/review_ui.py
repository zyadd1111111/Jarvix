"""Render real UI workspaces using a disposable, offline review profile."""
import argparse
import os
from pathlib import Path
import time
import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from jarvix.services import Services
from jarvix.ui.window import MainWindow
from jarvix.ui.workflows import WorkflowBuilder


class ReviewVault:
    def get(self, _):
        return None


def settle(app, window):
    deadline = time.monotonic() + 12
    while (window.jobs or (window.closing and window.isVisible())) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if window.jobs or (window.closing and window.isVisible()):
        raise TimeoutError("Review work or window shutdown did not finish")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(120)
    app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    parser.add_argument("--empty", action="store_true")
    args = parser.parse_args()
    output = Path("artifacts/ui09") / (f"{args.width}x{args.height}-scale{os.environ.get('QT_SCALE_FACTOR', '1')}"
                                      + ("-empty" if args.empty else ""))
    output.mkdir(parents=True, exist_ok=True)
    profile = output / ("profile-" + uuid.uuid4().hex)
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI Variable", 10))
    app.setQuitOnLastWindowClosed(False)
    services = Services(profile, vault=ReviewVault())
    workflow = None
    if not args.empty:
        project_dir = profile / "Jarvix"
        project_dir.mkdir()
        (project_dir / "release-checklist.md").write_text("# Release review\n\nVerify package and permissions.\n", encoding="utf-8")
        (project_dir / "browser_bridge.py").write_text("def reconnect(peer):\n    return peer.connect()\n", encoding="utf-8")
        services.add_file_root(str(project_dir))
        services.scan_files()
        project = services.add_project("Jarvix", str(project_dir))
        task = services.add_task("Verify Windows package", (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat())
        services.add_task("Review browser connection errors")
        note = services.save_note("Browser reconnect checklist", "Inspect connected peers and retry a verified read.\n\nNever replay a pending write.")
        space = services.knowledge_spaces.create("Jarvix release", project_id=project)
        services.knowledge_spaces.add_source(space["id"], "note", note)
        services.missions.save("Prepare Jarvix for release", project_id=project, task_ids=[task], note_ids=[note],
            blockers=["Package verification is pending"], next_steps=["Run the package startup check"],
            milestones=[{"id": "review", "title": "Review interface", "status": "complete"},
                        {"id": "package", "title": "Verify executable", "status": "pending"}])
        workflow = services.workflows.save("Review today's work", [
            {"kind": "action", "tool": "tasks.list", "arguments": {}},
            {"kind": "action", "tool": "notes.search", "arguments": {"query": "browser"}}],
            kind="routine", enabled=False)
        proposal = services.skills.learn_preview("Review work", routine_id=workflow["id"])
        services.skills.save("Review work", proposal["review_fingerprint"], routine_id=workflow["id"])
        services.operator.run({"goal": "Inspect unfinished tasks", "steps": [
            {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
        conversation = services.new_conversation("Browser connection review")
        services.repository.append_message(conversation, "user", "How should I verify the browser reconnect change?")
        services.repository.append_message(conversation, "assistant", "Check a fresh read after reconnect, then confirm that pending writes are not replayed.\n\n```python\nassert peer.connected\nassert pending_write.status == 'awaiting_review'\n```\n\n| Check | Expected result |\n| --- | --- |\n| Read | Completes |\n| Pending write | Requires review |")
    with patch.object(MainWindow, "refresh_system", lambda self: None), patch.object(MainWindow, "run_routines", lambda self: None):
        window = MainWindow(services)
        window.snapshot = services.system_snapshot()
        window.resize(args.width, args.height)
        window.show()
        try:
            for name in ("Home", "Chat", "Operator", "Missions", "Knowledge", "Skills", "Projects", "Files",
                         "Automations", "Integrations", "System", "Settings", "Tasks", "Notes", "Apps", "Voice", "Memory", "Activity"):
                window.navigate(name)
                if name == "Chat" and not args.empty:
                    window.pages[name].load_conversation(conversation)
                    window.pages[name].on_activity("tool_result", {"name": "tasks.list", "ok": True,
                                                                 "data": {"items": services.list_tasks()}})
                settle(app, window)
                path = output / (name.lower() + ".png")
                if not window.grab().save(str(path)):
                    raise OSError("Could not save " + str(path))
                print(f"{name}: {window.width()}x{window.height()} · {path}")
            if workflow:
                builder = WorkflowBuilder(window, workflow)
                builder.show()
                settle(app, window)
                builder.grab().save(str(output / "workflow.png"))
                builder.close()
        finally:
            window.close()
            settle(app, window)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            services.close()


if __name__ == "__main__":
    main()
