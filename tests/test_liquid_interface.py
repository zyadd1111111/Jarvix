"""NEW changes presentation only, including after an existing profile migrates."""
import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from jarvix.domain import PermissionRequest
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.theme import STYLESHEET
from test_interface_modes import opened_interface
from test_ui import app as app


@pytest.mark.parametrize("previous", ["legacy", "nexus"])
def test_old_preserves_the_selected_checkpoint_after_new_is_disabled(app, tmp_path, monkeypatch, previous):
    profile = tmp_path / "profile"
    with opened_interface(app, monkeypatch, profile, previous) as current:
        selector = current.pages["Settings"].interface_selector
        assert [selector.itemText(i) for i in range(selector.count())] == ["OLD", "NEW"]
        selector.setCurrentIndex(selector.findData("new"))
        assert current.services.settings.get("ui.old_interface") == previous
        assert current.interface_mode == previous
    with opened_interface(app, monkeypatch, profile) as new:
        assert new.interface_mode == "new"
        assert new.pages["Chat"].services is new.services
        new.pages["Settings"].interface_selector.setCurrentIndex(0)
    with opened_interface(app, monkeypatch, profile) as old:
        assert old.interface_mode == previous
        if previous == "legacy":
            assert old.styleSheet() == STYLESHEET


def test_new_permission_sheet_preserves_deny_default_and_keyboard_denial(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        request = PermissionRequest("execute", "files.recycle", "filesystem.write", "Recycle one file",
                                    {"path": str(tmp_path / "report.txt")})
        dialog = PermissionDialog(request, window)
        dialog.show()
        app.processEvents()
        assert dialog.property("nexusSheet") and dialog.deny_button.isDefault()
        assert not dialog.allow_button.isDefault()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert not window.services.settings.get("control.enabled", False)
        dialog.deleteLater()


def test_new_does_not_edit_existing_presentations_or_backend():
    root = Path(__file__).resolve().parents[1]
    baseline = root / "artifacts/liquid-current-baseline.json"
    if not baseline.exists():
        pytest.skip("Local before-change integrity evidence is not shipped")
    hashes = json.loads(baseline.read_text())
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == expected
               for path, expected in hashes.items())
