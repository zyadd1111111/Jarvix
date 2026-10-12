"""Exercise real NEW widgets and measure native motion in an isolated local profile."""
import argparse
import json
from pathlib import Path
import statistics
import time
import uuid
from unittest.mock import patch

import psutil
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QPlainTextEdit, QPushButton

from jarvix.domain import PermissionRequest
from jarvix.services import Services
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.interface import create_window
from jarvix.ui.liquid import motion
from jarvix.ui.window import MainWindow
from review_nexus import seed
from review_ui import ReviewVault, settle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=("cream", "frost", "graphite", "sand"), default="cream")
    args = parser.parse_args()
    output = Path("artifacts/nexus-interactions") / args.theme
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(["Jarvix interaction review", "-platform", "windows"])
    app.setFont(QFont("Segoe UI Variable", 10))
    app.setCursorFlashTime(0)
    app.setQuitOnLastWindowClosed(False)
    services = Services(output / ("profile-" + uuid.uuid4().hex), vault=ReviewVault())
    conversation = seed(services, services.data_dir)
    services.settings.set("ui.interface", "new")
    services.settings.set("liquid.theme", args.theme)
    services.settings.set("liquid.accent", "theme")
    process = psutil.Process()
    results = {}
    with patch.object(MainWindow, "refresh_system", lambda self: None), patch.object(MainWindow, "run_routines", lambda self: None):
        window = create_window(services)
        window.resize(1280, 720)
        window.show()
        try:
            settle(app, window)
            def capture(name, widget=window):
                app.processEvents()
                assert widget.grab().save(str(output / (name + ".png")))
            expanded = window.sidebar.width()
            window.sidebar_toggle.click()
            QTest.qWait(60)
            assert 58 < window.sidebar.width() < expanded
            capture("sidebar-in-motion")
            QTest.qWait(220)
            assert window.sidebar.width() == 58
            window.sidebar_toggle.click()
            QTest.qWait(240)
            assert window.sidebar.width() == expanded
            results["sidebar_motion"] = True

            window.navigate("Chat")
            QTest.qWait(60)
            assert window.stack.findChildren(motion.Transition)
            capture("page-in-motion")
            QTest.qWait(220)
            chat = window.pages["Chat"]
            chat.load_conversation(conversation)
            settle(app, window)
            assert window.sidebar.history.maximumHeight() < 190
            assert chat.history_pane.isHidden() and window.sidebar.history.isVisible()
            chat.composer.setPlainText("Preserve this unsent draft")
            chat.model_toggle.click()
            QTest.qWait(240)
            assert chat.model_settings.isVisible()
            capture("chat-model-inspector")
            chat.model_toggle.click()
            QTest.qWait(240)
            assert chat.model_settings.isHidden()
            chat.on_activity("tool_result", {"name": "tasks.list", "ok": True, "data": {"items": services.list_tasks()}})
            chat.actions_toggle.click()
            QTest.qWait(240)
            assert chat.timeline.isVisible()
            capture("chat-action-details")
            chat.composer.setFocus()
            QTest.qWait(180)
            capture("composer-focus")
            results["conversation_controls"] = True

            chat.add_button.menu().popup(chat.add_button.mapToGlobal(chat.add_button.rect().bottomLeft()))
            QTest.qWait(180)
            assert chat.attachment_menu.isVisible()
            capture("attachment-menu", chat.attachment_menu)
            QTest.keyClick(chat.attachment_menu, Qt.Key.Key_Escape)
            assert chat.attachment_menu.isHidden()
            chat.show_inspector(0)
            QTest.qWait(240)
            assert chat.provider.isVisible()
            chat.provider.showPopup()
            QTest.qWait(180)
            assert chat.provider.view().isVisible()
            capture("model-picker")
            QTest.keyClick(chat.provider.view(), Qt.Key.Key_Escape)
            chat.provider.hidePopup()
            chat.close_inspector()
            results["attachment_and_model_menus"] = True

            def dismiss_palette():
                dialog = next(dialog for dialog in window.findChildren(QDialog) if dialog.isVisible())
                capture("command-palette", dialog)
                QTest.keyClick(dialog, Qt.Key.Key_Escape)
            QTimer.singleShot(180, dismiss_palette)
            window.open_palette()
            assert chat.composer.toPlainText() == "Preserve this unsent draft"
            results["palette_keyboard_dismissal"] = True

            window.navigate("Files")
            settle(app, window)
            files = window.pages["Files"]
            files.entries.setCurrentCell(0, 0)
            rect = files.entries.visualItemRect(files.entries.item(0, 0))
            menu_seen = []
            def dismiss_menu():
                menu = next(menu for menu in window.findChildren(QMenu) if menu.isVisible())
                capture("file-context-menu", menu)
                menu_seen.append(True)
                QTest.keyClick(menu, Qt.Key.Key_Escape)
            QTimer.singleShot(180, dismiss_menu)
            files.entries.customContextMenuRequested.emit(rect.center())
            assert menu_seen
            results["file_menu"] = True

            window.navigate("Operator")
            settle(app, window)
            operator = window.pages["Operator"]
            plan = {"goal": "Review local tasks and notes", "steps": [
                {"id": "tasks", "tool": "tasks.list", "arguments": {}},
                {"id": "notes", "tool": "notes.search", "arguments": {"query": "browser"}, "depends_on": ["tasks"]}]}
            def approve_preview():
                dialog = operator.pending_dialog
                if dialog is not None:
                    preview = json.loads(dialog.findChild(QPlainTextEdit).toPlainText())
                    assert [step["tool"] for step in preview["steps"]] == ["tasks.list", "notes.search"]
                    assert all(step["permission_level"] == 1 for step in preview["steps"])
                    capture("operator-plan-preview", dialog)
                    dialog.allow_button.click()
            approval_timer = QTimer(window)
            approval_timer.timeout.connect(approve_preview)
            approval_timer.start(180)
            operator.start(lambda **kwargs: services.operator.run(plan, **kwargs))
            settle(app, window)
            approval_timer.stop()
            assert operator.worker is None and operator.steps.count() == 2
            session = services.operator.get(operator.session_id)
            assert session["status"] == "complete" and all(step["status"] == "complete" for step in session["steps"])
            capture("operator-completed")
            operator.inspector_toggle.click()
            QTest.qWait(240)
            operator.inspector_toggle.click()
            QTest.qWait(240)
            operator.graph_button.click()
            QTest.qWait(240)
            capture("operator-dependencies")
            results["real_read_only_operator"] = True

            request = PermissionRequest("execute", "files.recycle", "filesystem.write", "Recycle one file",
                                        {"path": "Review file"})
            dialog = PermissionDialog(request, window)
            dialog.show()
            QTest.qWait(180)
            assert dialog.deny_button.isDefault()
            capture("permission-dialog", dialog)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            assert dialog.result() == QDialog.DialogCode.Rejected
            dialog.deleteLater()
            results["confirmation_denied"] = True

            window.navigate("Settings")
            settle(app, window)
            settings = window.pages["Settings"]
            settings.categories.setCurrentRow(next(i for i in range(settings.categories.count())
                if settings.categories.item(i).text() == "Appearance"))
            capture("appearance")
            window.notify("2 local checks completed")
            QTest.qWait(200)
            capture("toast")
            next(control for control in window._toast.findChildren(QPushButton) if control.text() == "Dismiss").click()
            assert window._toast.isHidden()
            services.settings.set("liquid.motion", False)
            window.configure_materials()
            window.toggle_sidebar(True)
            assert window.sidebar.width() == 58
            window.toggle_sidebar(False)
            assert window.sidebar.width() == expanded
            results["reduced_motion"] = True
            services.settings.set("liquid.motion", True)
            window.configure_materials()
            window.navigate("Home")
            QTest.qWait(240)

            start = time.perf_counter()
            cpu = sum(process.cpu_times()[:2])
            QTest.qWait(2500)
            results["idle_cpu_percent_one_core"] = round((sum(process.cpu_times()[:2]) - cpu) / (time.perf_counter() - start) * 100, 2)
            cpu = sum(process.cpu_times()[:2])
            start = time.perf_counter()
            frames = []
            frame_counts = []
            def observed_frame(_):
                frames.append(time.perf_counter())
            for _ in range(8):
                for collapsed in (True, False):
                    frames.clear()
                    window.toggle_sidebar(collapsed)
                    window.sidebar._liquid_animations["width"].valueChanged.connect(observed_frame)
                    QTest.qWait(230)
                    frame_counts.append(len(frames))
            results["median_sidebar_animation_frames"] = statistics.median(frame_counts)
            results["animation_cpu_percent_one_core"] = round((sum(process.cpu_times()[:2]) - cpu) / (time.perf_counter() - start) * 100, 2)
            window.navigate("Chat")
            settle(app, window)
            timings = []
            scrollbar = chat.transcript.verticalScrollBar()
            for i in range(40):
                start = time.perf_counter()
                scrollbar.setValue(scrollbar.maximum() * (i % 2))
                app.processEvents()
                chat.transcript.grab()
                timings.append((time.perf_counter() - start) * 1000)
            results["scroll_capture_p95_ms"] = round(sorted(timings)[int(len(timings) * .95) - 1], 2)
            timings = []
            for width, height in ((1000, 650), (1280, 720), (1500, 900)) * 4:
                start = time.perf_counter()
                window.resize(width, height)
                app.processEvents()
                window.grab()
                timings.append((time.perf_counter() - start) * 1000)
            results["resize_capture_median_ms"] = round(statistics.median(timings), 2)
            results["rss_mb"] = round(process.memory_info().rss / 1024**2, 1)
            results["native_backdrop"] = window.composition_status
            results["theme"] = args.theme
            results["notes"] = "Native Windows, isolated profile. Read-only Operator tools; menus and permission dialogs dismissed. No cloud requests."
            (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(results, indent=2), flush=True)
        finally:
            window.close()
            settle(app, window)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            services.close()


if __name__ == "__main__":
    main()
