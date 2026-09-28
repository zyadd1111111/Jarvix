import struct
from types import SimpleNamespace

import pytest

from jarvix.capabilities.desktop_vision import DesktopVisionService
from jarvix.records import RecordStore
from jarvix.storage import Database, SettingsRepository


@pytest.fixture
def vision(tmp_path):
    database = Database(tmp_path / "test.db")
    services = SimpleNamespace(settings=SettingsRepository(database), records=RecordStore(database), data_dir=tmp_path)
    services.settings.set("screenshots.enabled", True)
    services.desktop = SimpleNamespace(active_window=lambda: {"handle": 1, "process_id": 2},
                                      inspect_ui=lambda *args, **kw: {"application": "Example", "bounded": False, "elements": []})
    services.screenshots = SimpleNamespace(capture=lambda *args: {"id": "image", "path": "local.bmp"})
    return DesktopVisionService(services)


def bitmap(vision, name, pixels, width=1, height=1):
    path = vision.services.data_dir / "screenshots" / f"{name}.bmp"
    path.parent.mkdir(exist_ok=True)
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    path.write_bytes(struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54) + header + pixels)
    vision.services.records.put("screenshot", {"path": str(path)}, name)
    return name


def test_capture_active_app_is_explicit_and_stays_local(vision):
    calls = []
    vision.services.screenshots.capture = lambda *args: calls.append(args) or {"id": "image"}
    result = vision.capture()
    assert calls == [("window", 0, 1, 2)]
    assert not result["external_upload"]
    assert vision.services.records.get("vision_capture", "image")["handle"] == 1


def test_capture_rejects_protected_window_before_pixels_are_read(vision):
    vision.services.desktop.inspect_ui = lambda *args, **kw: {"elements": [{"password": True}]}
    with pytest.raises(PermissionError):
        vision.capture()
    assert not vision.services.records.list("vision_capture")


def test_inspection_reports_accessibility_source_and_error_candidates_honestly(vision):
    vision.services.desktop.inspect_ui = lambda *args, **kw: {
        "application": "Editor", "bounded": False,
        "elements": [{"name": "Error: file missing", "control_type": "Text", "enabled": True},
                     {"name": "Retry", "control_type": "Button", "element_ref": "known", "enabled": True},
                     {"name": "secret", "control_type": "Edit", "password": True}],
    }
    result = vision.inspect(1, 2)
    assert result["possible_errors"] == ["Error: file missing"]
    assert not result["ocr_performed"] and not result["image_inference_performed"]
    assert "secret" not in str(result)


def test_compare_real_pixels_ignores_alpha_and_does_not_infer_completion(vision):
    before = bitmap(vision, "before", bytes([1, 2, 3, 0]))
    equal = bitmap(vision, "equal", bytes([1, 2, 3, 255]))
    changed = bitmap(vision, "changed", bytes([1, 2, 4, 0]))
    assert not vision.compare(before, equal)["changed"]
    result = vision.compare(before, changed)
    assert result["changed_pixels"] == 1 and result["changed_percent"] == 100
    assert result["completion_verified"] is False


def test_compare_rejects_external_paths_invalid_bitmap_and_revoked_access(vision):
    external = vision.services.data_dir / "outside.bmp"
    external.write_bytes(b"BM")
    vision.services.records.put("screenshot", {"path": str(external)}, "outside")
    with pytest.raises(PermissionError):
        vision.compare("outside", "outside")
    invalid = bitmap(vision, "invalid", b"bad")
    with pytest.raises(ValueError):
        vision.compare(invalid, invalid)
    vision.services.settings.set("screenshots.enabled", False)
    with pytest.raises(PermissionError):
        vision.inspect(1, 2)


def test_compare_dimension_changes_are_not_a_pixel_percentage(vision):
    first = bitmap(vision, "first", b"\0" * 4)
    second = bitmap(vision, "second", b"\0" * 8, width=2)
    result = vision.compare(first, second)
    assert result["changed"] and not result["same_dimensions"]
    assert "changed_percent" not in result


def test_ocr_uses_validated_image_bytes_and_suppresses_credentials(vision, monkeypatch):
    import base64

    screenshot_id = bitmap(vision, "ocr", bytes([1, 2, 3, 0]))
    requests = []

    def worker(script, arguments, checkpoint, **limits):
        requests.append((arguments, limits))
        checkpoint()
        assert base64.b64decode(arguments["bitmap"])[:2] == b"BM"
        assert "path" not in arguments
        return {"available": True, "language": "en-US", "bounded": False,
                "lines": ["Error: missing document", "Password", "do not disclose",
                          "Try reopening the file", "api_key=should-be-private"]}

    monkeypatch.setattr("jarvix.capabilities.windows_uia.run_native_script", worker)
    result = vision.read_text(screenshot_id)
    assert result["ocr_performed"] and result["external_upload"] is False
    assert "do not disclose" not in str(result) and "should-be-private" not in str(result)
    assert "Error: missing document" in result["text"]
    assert requests[0][1]["timeout"] == 25


def test_ocr_unsupported_and_revoked_access_fail_without_disclosing_output(vision, monkeypatch):
    screenshot_id = bitmap(vision, "ocr", bytes([1, 2, 3, 0]))
    monkeypatch.setattr("jarvix.capabilities.windows_uia.run_native_script", lambda *a, **k: {"available": False})
    with pytest.raises(RuntimeError, match="unavailable"):
        vision.read_text(screenshot_id)

    def revoked(*args, **kwargs):
        vision.services.settings.set("screenshots.enabled", False)
        return {"available": True, "lines": ["text that must not be returned"]}

    monkeypatch.setattr("jarvix.capabilities.windows_uia.run_native_script", revoked)
    with pytest.raises(PermissionError):
        vision.read_text(screenshot_id)


def test_bounded_protection_inspection_cannot_authorize_window_capture(vision):
    vision.services.desktop.inspect_ui = lambda *a, **k: {"elements": [], "bounded": True}
    with pytest.raises(PermissionError, match="fully checked"):
        vision.capture()
    assert vision.services.records.list("vision_capture") == []

