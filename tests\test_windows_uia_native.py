"""Opt-in integration checks against a temporary window owned by this test process."""
import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("JARVIX_NATIVE_UIA_TEST") != "1",
    reason="Requires explicit native Windows UIA test opt-in.",
)


def test_real_uia_controls_capture_ocr_and_stale_identity_rejection():
    # A separate QApplication with its normal event loop prevents interference
    # from the offscreen UI fixtures and permits COM accessibility callbacks.
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_operator_native.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            encoding="utf-8", timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["success"] and report["control_actions"] and report["stale_identity"]
    assert report["window_capture"] and report["ocr"]["test_text_found"]
    assert report["ocr"]["external_upload"] is False
