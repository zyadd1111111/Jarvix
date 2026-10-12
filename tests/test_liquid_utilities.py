"""The remaining NEW workspaces reuse their existing data and edit controllers."""
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QTabWidget

from test_interface_modes import opened_interface
from test_ui import app as app


def test_liquid_record_workspaces_keep_edits_and_selection_metadata(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        tasks = window.pages["Tasks"]
        window.navigate("Tasks")
        tasks.view.setCurrentIndex(tasks.view.findData("all"))
        tasks.entry.setText("Review UI")
        tasks.add()
        tasks.entries.selectRow(0)
        assert tasks.entries.item(0, 1).text() == "Review UI"
        tasks.complete()
        assert window.services.list_tasks()[0]["status"] in ("done", "completed")
        notes = window.pages["Notes"]
        window.navigate("Notes")
        notes.title_edit.setText("Design review")
        notes.body.setPlainText("Keep the existing permission dialogs.")
        assert notes.dirty
        notes.save()
        assert window.services.list_notes()[0]["title"] == "Design review"
        assert not notes.dirty
        assert not window.services.settings.get("control.enabled", False)
        for name in ("Tasks", "Notes", "Memory", "Projects", "Activity", "Voice"):
            window.navigate(name)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            window.pages[name].refresh()
            assert not window.grab().isNull()


def test_liquid_utility_panes_do_not_force_laptop_window_to_expand(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.resize(860, 600)
        for name in ("Tasks", "Notes", "Memory", "Projects", "Activity", "Voice"):
            window.navigate(name)
            app.processEvents()
            assert window.width() == 860
            page = window.pages[name]
            assert page.layout.geometry().width() <= page.width()
        voice = window.pages["Voice"]
        tabs = voice.findChild(QTabWidget)
        assert [tabs.tabText(i) for i in range(tabs.count())] == ["Dictation", "Playback"]
        assert tabs.widget(0).isAncestorOf(voice.input_panel)
        app.processEvents()
        assert voice.input_panel.width() >= voice.input_panel.minimumSizeHint().width()
