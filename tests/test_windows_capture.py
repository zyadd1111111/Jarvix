import base64
from types import SimpleNamespace

import pytest

from jarvix.capabilities.native_windows import Win32
from jarvix.capabilities.windows_capture import capture_window


def test_isolated_window_capture_encodes_validated_pixels_and_bounds(monkeypatch):
    calls = []

    def worker(script, arguments, **options):
        calls.append((arguments, options))
        return {"pixels": base64.b64encode(bytes([1, 2, 3, 0])).decode("ascii")}

    monkeypatch.setattr("jarvix.capabilities.windows_capture.run_native_script", worker)
    data = capture_window(2**40, 42, -100, 0, 1, 1)
    assert data[:2] == b"BM" and len(data) == 58
    assert calls[0][0] == {"handle": 2**40, "process_id": 42, "x": -100, "y": 0, "width": 1, "height": 1}
    assert calls[0][1]["timeout"] == 12
    monkeypatch.setattr("jarvix.capabilities.windows_capture.run_native_script", lambda *a, **k: {"pixels": ""})
    with pytest.raises(ValueError, match="invalid capture dimensions"):
        capture_window(2**40, 42, 0, 0, 1, 1)


def test_window_capture_failure_never_reads_desktop_pixels(monkeypatch):
    native = Win32.__new__(Win32)

    def bounds(handle, pointer):
        pointer._obj.left, pointer._obj.top = 0, 0
        pointer._obj.right, pointer._obj.bottom = 10, 10
        return True

    native.user = SimpleNamespace(GetWindowRect=bounds)
    native.window = lambda handle: {"process_id": 42, "minimized": False}
    native._capture_bitmap = lambda *a: pytest.fail("Window capture must never crop desktop pixels")

    def unsupported(*args, **kwargs):
        raise ValueError("Application does not support capture")

    monkeypatch.setattr("jarvix.capabilities.windows_capture.capture_window", unsupported)
    with pytest.raises(ValueError, match="does not support"):
        native.screenshot("window", handle=123, process_id=42)
    with pytest.raises(ValueError, match="unavailable"):
        native.screenshot("window", handle=123, process_id=99)

