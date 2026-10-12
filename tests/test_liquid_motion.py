"""Finite NEW-only transitions preserve real widget state under interruption."""
from contextlib import contextmanager
import gc
import weakref

import pytest

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QSplitter, QTreeWidget, QVBoxLayout, QWidget

from jarvix.ui.liquid import motion
from jarvix.ui.liquid.components import LiquidPopover
from jarvix.ui.liquid.theme import TOKENS
from test_interface_modes import opened_interface
from test_ui import app as app, wait_until


@contextmanager
def presentation_root(app):
    root = QWidget()
    root.interface_mode = "new"
    root._liquid_preferences = {"motion": True}
    root.resize(640, 360)
    root.show()
    app.processEvents()
    try:
        yield root
    finally:
        motion.settle(root)
        root.close()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_new_motion_replaces_running_transition_without_stale_completion(app):
    with presentation_root(app) as root:
        progress = QProgressBar(root)
        progress.setRange(0, 100)
        completed = []
        first = motion.animate(progress, "progress", 0, 100, progress.setValue,
                               lambda: completed.append("obsolete"), duration=180)
        QTest.qWait(40)
        assert 0 < progress.value() < 100
        replacement = motion.animate(progress, "progress", progress.value(), 25, progress.setValue,
                                     lambda: completed.append("current"), duration=60)
        assert first.state() == QAbstractAnimation.State.Stopped
        assert replacement is not first
        wait_until(app, lambda: not progress._liquid_animations)
        QTest.qWait(180)
        assert progress.value() == 25 and completed == ["current"]


@pytest.mark.parametrize("ending", ["complete", "replace", "settle"])
def test_animation_releases_callback_owner_after_ending(app, ending):
    with presentation_root(app) as root:
        progress = QProgressBar(root)
        class Observer:
            def record(self, value):
                progress.setValue(value)
        observer = Observer()
        reference = weakref.ref(observer)
        motion.animate(progress, "progress", 0, 100, observer.record, duration=40)
        del observer
        assert reference() is not None
        if ending == "complete":
            wait_until(app, lambda: not progress._liquid_animations)
        elif ending == "replace":
            motion.animate(progress, "progress", 0, 25, progress.setValue, duration=40)
        else:
            motion.settle(root)
        gc.collect()
        assert reference() is None, "Finished/cancelled motion must not retain its callback owner"


def test_reduced_motion_settles_active_drawer_and_restores_limits(app):
    with presentation_root(app) as root:
        layout = QVBoxLayout(root)
        drawer = QWidget(root)
        drawer.setMinimumHeight(80)
        drawer.setMaximumHeight(180)
        QVBoxLayout(drawer).addWidget(QLabel("Context inspector", drawer))
        layout.addWidget(drawer)
        app.processEvents()
        motion.reveal(drawer, False)
        assert drawer.minimumHeight() == 0 and drawer.isVisible()
        root._liquid_preferences["motion"] = False
        motion.settle(root)
        assert drawer.isHidden()
        assert (drawer.minimumHeight(), drawer.maximumHeight()) == (80, 180)
        assert not drawer._liquid_animations
        motion.reveal(drawer, True)
        assert drawer.isVisible() and not drawer._liquid_animations
        assert (drawer.minimumHeight(), drawer.maximumHeight()) == (80, 180)


def test_splitter_reversal_restores_original_constraints_and_visibility(app):
    with presentation_root(app) as root:
        layout = QVBoxLayout(root)
        split = QSplitter(Qt.Orientation.Horizontal, root)
        split.setChildrenCollapsible(False)
        layout.addWidget(split)
        split.addWidget(QLabel("Document", split))
        inspector = QTreeWidget(split)
        inspector.setMinimumSize(96, 70)
        inspector.setMaximumSize(280, 320)
        split.addWidget(inspector)
        split.setCollapsible(1, False)
        app.processEvents()
        split.setSizes([380, 240])
        motion.reveal(inspector, False)
        QTest.qWait(40)
        motion.reveal(inspector, True)
        wait_until(app, lambda: not inspector._liquid_animations)
        assert inspector.isVisible()
        assert inspector.minimumSize() == QSize(96, 70)
        assert inspector.maximumSize() == QSize(280, 320)
        assert inspector.width() >= 96
        assert not split.isCollapsible(1)
        motion.reveal(inspector, False)
        wait_until(app, lambda: inspector.isHidden())
        assert inspector.minimumSize() == QSize(96, 70)
        motion.reveal(inspector, True)
        wait_until(app, lambda: not inspector._liquid_animations)
        assert inspector.isVisible() and inspector.width() >= 96


def test_splitter_hides_smoothly_before_final_visibility_change(app):
    with presentation_root(app) as root:
        layout = QVBoxLayout(root)
        split = QSplitter(Qt.Orientation.Horizontal, root)
        split.setChildrenCollapsible(False)
        layout.addWidget(split)
        split.addWidget(QLabel("Content", split))
        inspector = QTreeWidget(split)
        inspector.setMinimumWidth(100)
        split.addWidget(inspector)
        split.setCollapsible(1, False)
        app.processEvents()
        split.setSizes([380, 240])
        motion.reveal(inspector, False)
        animation = inspector._liquid_animations["reveal"]
        animation.setCurrentTime(TOKENS["duration_panel"] - 10)
        assert inspector.isVisible()
        assert inspector.width() < 20, "Noncollapsible size hints must not make inspectors jump closed"
        motion.settle(root)
        assert inspector.isHidden() and not split.isCollapsible(1)


def test_dialog_owner_ancestry_reduced_motion_and_old_scope(app):
    with presentation_root(app) as root:
        dialog = QDialog(root)
        dialog.window = root  # Existing work dialogs expose their application owner this way.
        control = QPushButton("Inspect context", dialog)
        assert motion.owner(control) is root and motion.owner(dialog) is root
        assert motion.enabled(control)
        root._liquid_preferences["motion"] = False
        assert not motion.enabled(control)
        completed = []
        assert motion.animate(control, "entry", 0, 1, completed.append) is None
        assert completed == [1]
        root._liquid_preferences["motion"] = True
        root.interface_mode = "legacy"
        assert not motion.enabled(control)
        motion.reveal(control, False)
        assert control.isHidden() and not getattr(control, "_liquid_animations", {})


def test_deleting_an_animated_child_does_not_run_a_stale_completion(app):
    with presentation_root(app) as root:
        progress = QProgressBar(root)
        progress.show()
        completed = []
        motion.animate(progress, "progress", 0, 100, progress.setValue,
                       lambda: completed.append("deleted control"), duration=80)
        progress.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QTest.qWait(120)
        assert not completed and not root.grab().isNull()


def test_deleting_an_animated_child_releases_callback_owner_on_gui_thread(app):
    with presentation_root(app) as root:
        child = QWidget(root)
        class Observer:
            def record(self, value):
                pass
        observer = Observer()
        reference = weakref.ref(observer)
        motion.animate(child, "entry", 0, 1, observer.record, duration=10_000)
        del observer
        assert reference() is not None
        child.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert reference() is None
        assert not child._liquid_animations


def test_new_notifications_keep_statusbar_reuse_toast_and_dismiss(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.notify("4 files moved")
        assert window.statusBar().currentMessage() == "4 files moved"
        toast = window._toast
        assert isinstance(toast, LiquidPopover) and toast.isVisible()
        assert window._toast_label.text() == "4 files moved"
        assert window.centralWidget().rect().contains(toast.geometry())
        window.notify("Tests failed")
        assert window._toast is toast and window._toast_label.text() == "Tests failed"
        window.resize(860, 600)
        app.processEvents()
        assert window.centralWidget().rect().contains(toast.geometry())
        dismiss = next(control for control in toast.findChildren(QPushButton) if control.text() == "Dismiss")
        dismiss.click()
        assert toast.isHidden()
        window.hide()
        window.notify("Background work paused")
        assert toast.isHidden() and window.statusBar().currentMessage() == "Background work paused"
    with opened_interface(app, monkeypatch, tmp_path / "old-profile", "old") as old:
        old.notify("4 files moved")
        assert old.statusBar().currentMessage() == "4 files moved"
        assert not hasattr(old, "_toast")
