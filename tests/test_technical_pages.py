"""Technical desktop pages keep real selection and permissioned action paths."""
from pathlib import Path

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QThread
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame, QPushButton, QWidget, QLabel, QVBoxLayout, QHBoxLayout
from shiboken6 import isValid, ownedByPython

from jarvix.ui.pages import selected_record
from jarvix.ui.widgets import clear_layout
from test_ui import app as app, window as window


def test_file_sorting_keeps_records_and_actions_use_review_forms(window, tmp_path, monkeypatch):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "small.txt").write_text("x", encoding="utf-8")
    (folder / "large.txt").write_text("x" * 4096, encoding="utf-8")
    window.services.add_file_root(str(folder))
    window.services.scan_files()
    page = window.pages["Files"]
    page.refresh()
    page.entries.sortItems(2, Qt.SortOrder.DescendingOrder)
    page.entries.selectRow(0)
    record = selected_record(page.entries)
    assert Path(record["path"]).name == "large.txt"
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    for action in ("open", "reveal", "copy_path", "move", "rename", "recycle"):
        page.file_action(action)
    assert calls[-3:] == [("files.move", {"source": str(folder / "large.txt")}),
                         ("files.rename", {"path": str(folder / "large.txt")}),
                         ("files.recycle", {"path": str(folder / "large.txt")})]
    assert (folder / "large.txt").exists()
    page.search.setText("not present")
    assert page.entries.rowCount() == 0 and "No matching" in page.status.text()


def test_home_command_and_categories_expose_real_actions_without_decoration(window, monkeypatch):
    page = window.pages["Home"]
    calls = []
    monkeypatch.setattr(window, "open_chat", calls.append)
    page.command.setText("Continue where I left off")
    page.submit()
    assert calls == ["Continue where I left off"] and not page.command.text()
    assert not page.findChildren(QFrame, "Hero")
    assert page.command.accessibleName() == "Universal Jarvix command"
    settings = window.pages["Settings"]
    assert settings.categories.count() == settings.settings_stack.count() == 8
    for index in range(settings.categories.count()):
        settings.categories.setCurrentRow(index)
        assert settings.settings_stack.currentIndex() == index
    assert set(settings.access_checks) == set(settings.access_defaults)
    assert not window.services.settings.get("control.enabled", False)


def test_system_sorting_inspects_the_selected_process_without_terminating(window, monkeypatch):
    window.snapshot = {"cpu_percent": 17, "memory_percent": 40, "memory_used_gb": 8,
        "memory_total_gb": 20, "processes": [{"name": "Small", "pid": 2, "memory_mb": 9},
                                             {"name": "Large", "pid": 3, "memory_mb": 200}]}
    page = window.pages["System"]
    page.refresh()
    assert page.cpu_value.text() == "17.0%" and page.memory_value.text() == "40.0%"
    page.processes.sortItems(2, Qt.SortOrder.DescendingOrder)
    page.processes.selectRow(0)
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    page.inspect_process()
    assert calls == [("processes.details", {"pid": 3})]


def test_removed_nested_layout_retains_qt_ownership_until_gui_deletion(app):
    frame = QWidget()
    outer = QVBoxLayout(frame)
    row = QHBoxLayout()
    text = QLabel("Local status", frame)
    row.addWidget(text)
    outer.addLayout(row)
    deleted_on = []
    row.destroyed.connect(lambda: deleted_on.append(QThread.currentThread()))
    clear_layout(outer)
    assert outer.count() == 0 and isValid(row)
    assert not ownedByPython(row), "Removed Qt layouts must not be collected by worker-thread Python GC."
    assert row.parent() is outer
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not isValid(row) and not isValid(text)
    assert deleted_on == [app.thread()]
    frame.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", [(1024, 700), (1280, 840)])
def test_home_rows_do_not_clip_after_refresh(window, app, size, tmp_path):
    root = tmp_path / "Jarvix project"
    root.mkdir()
    window.services.add_file_root(str(root))
    project = window.services.add_project("Jarvix", str(root))
    task = window.services.add_task("Verify Windows package", "2026-10-05T17:00:00-04:00")
    note = window.services.save_note("Release checklist", "Build and verify artifacts")
    window.services.missions.save("Prepare Jarvix for release", project_id=project, task_ids=[task],
        note_ids=[note], milestones=[{"id": "build", "title": "Build package"},
                                    {"id": "verify", "title": "Verify startup"}])
    window.resize(*size)
    page = window.pages["Home"]
    page.refresh()
    app.processEvents()
    QTest.qWait(120)
    app.processEvents()
    for control in page.findChildren(QPushButton):
        if control.isVisible():
            current = control
            while current is not page.scroll_body and current is not page:
                parent = current.parentWidget()
                assert current.geometry().bottom() < parent.height(), control.text()
                assert current.geometry().right() < parent.width(), control.text()
                current = parent
