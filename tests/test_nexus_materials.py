"""Glass remains local, cached, optional and outside the permission boundary."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from jarvix.domain import PermissionRequest
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.nexus import composition
from jarvix.ui.nexus.materials import GlassSurface
from test_interface_modes import opened_interface
from test_ui import app as app


def test_glass_is_cached_and_solid_reduced_motion_controls_apply(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "nexus") as window:
        QTest.qWait(220)
        window.grab()
        surface = window.findChild(GlassSurface)
        assert surface._cache_key is not None
        cached = surface._backdrop.cacheKey()
        window.grab()
        assert surface._backdrop.cacheKey() == cached
        settings = window.pages["Settings"]
        settings.transparency.setChecked(False)
        settings.reduced_motion.setChecked(True)
        settings.glass_intensity.setCurrentIndex(settings.glass_intensity.findData("solid"))
        assert window._nexus_preferences == {"transparency": False, "motion": False, "glass": "solid"}
        window.navigate("Chat")
        assert window._transition is None
        assert not window.services.settings.get("screenshots.enabled", False)


def test_nexus_permission_sheet_preserves_exact_preview_and_escape_denies(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "nexus") as window:
        request = PermissionRequest("execute", "files.recycle", "filesystem.write", "Recycle report.txt",
            {"path": str(tmp_path / "report.txt")}, preview="Exact action preview")
        dialog = PermissionDialog(request, window)
        dialog.show()
        app.processEvents()
        assert dialog.property("nexusSheet")
        assert dialog.allow_button.text() == "Allow once" and dialog.deny_button.isDefault()
        assert dialog.allow_button.isVisible() and dialog.deny_button.isVisible()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == QDialog.DialogCode.Rejected
        dialog.deleteLater()
        assert not window.services.records.list("file_recycle")


def test_windows_backdrop_has_a_non_windows_fallback(monkeypatch):
    monkeypatch.setattr(composition.sys, "platform", "linux")
    assert composition.apply_backdrop(None) == "Qt material"
