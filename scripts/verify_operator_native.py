"""Opt-in native smoke: inspects and captures only windows created by this script."""
import json
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from jarvix.capabilities.computer import ScreenshotService
from jarvix.capabilities.desktop import DesktopOperatorService
from jarvix.capabilities.desktop_vision import DesktopVisionService
from jarvix.capabilities.native_windows import Win32
from jarvix.records import RecordStore
from jarvix.storage import Database, SettingsRepository


app = QApplication([])
window = QWidget()
window.setWindowTitle("Jarvix owned native verification")
layout = QVBoxLayout(window)
indicator = QLabel("Jarvix native verification · idle")
layout.addWidget(indicator)
label = QLabel("JARVIX LOCAL OCR TEST\nError: sample document missing")
label.setStyleSheet("font-size: 25px; color: black; background: white;")
layout.addWidget(label)
editor = QLineEdit("ordinary text")
editor.setAccessibleName("Document")
layout.addWidget(editor)
password = QLineEdit("DO_NOT_DISCLOSE_TEST")
password.setAccessibleName("Password")
password.setEchoMode(QLineEdit.EchoMode.Password)
layout.addWidget(password)
button = QPushButton("Save")
button.clicked.connect(lambda: button.setText("Saved smoke document"))
layout.addWidget(button)
window.resize(650, 350)
window.move(40, 40)
window.show()
cover = QWidget()
cover.setWindowTitle("Jarvix owned occlusion test")
cover.setStyleSheet("background: red;")
cover_layout = QVBoxLayout(cover)
cover_layout.addWidget(QLabel("OTHER WINDOW CONTENT MUST NOT BE CAPTURED"))
cover.setGeometry(60, 70, 620, 310)
ready = threading.Event()
report = {}


class Bridge(QObject):
    capture_ready = Signal()
    indicator_requested = Signal(object)
    finished = Signal()


bridge = Bridge()


def show_indicator(request):
    state, acknowledged = request
    indicator.setText("Jarvix is controlling this test window" if state.get("active")
                      else "Jarvix native verification · idle")
    QTimer.singleShot(50, acknowledged.set)


def acknowledge_indicator(state):
    acknowledged = threading.Event()
    bridge.indicator_requested.emit((state, acknowledged))
    return acknowledged.wait(5)


def prepare_capture():
    password.hide()
    cover.show()
    cover.raise_()
    QTimer.singleShot(250, ready.set)


bridge.capture_ready.connect(prepare_capture)
bridge.indicator_requested.connect(show_indicator)
bridge.finished.connect(app.quit)


def verify():
    try:
        with tempfile.TemporaryDirectory(prefix="jarvix-native-smoke-") as directory:
            data_dir = Path(directory)
            db = Database(data_dir / "smoke.db")
            native = Win32()
            handle, pid = int(window.winId()), os.getpid()
            # Never enumerate, inspect, activate or capture any user's windows.
            windows = SimpleNamespace(list=lambda query="": [native.window(handle)],
                                      foreground=lambda: native.window(handle))
            services = SimpleNamespace(data_dir=data_dir, settings=SettingsRepository(db),
                                       records=RecordStore(db), windows=windows)
            services.settings.set("screenshots.enabled", True)
            services.desktop = desktop = DesktopOperatorService(services)
            desktop.set_indicator(acknowledge_indicator)
            services.screenshots = ScreenshotService(services, native)
            services.vision = vision = DesktopVisionService(services)
            tree = desktop.inspect_ui(handle, pid)
            assert any(row["password"] for row in tree["elements"]), tree
            assert "DO_NOT_DISCLOSE_TEST" not in str(tree)
            assert desktop.find_element(handle, pid, "Save", exact=True)["count"] == 1
            assert desktop.find_element(handle, pid, "Document", exact=True)["count"] == 1
            assert desktop.inspect_ui(handle, pid, limit=1)["bounded"]
            report["uia"] = "targeted controls, password omission and bounded-tree detection passed"
            identity = desktop.backend.call("inspect", handle=handle, process_id=pid, limit=1)
            for changed in ({"process_started": "0", "root_id": identity["root_id"]},
                            {"process_started": identity["process_started"], "root_id": "replaced"}):
                try:
                    desktop.backend.call("bounds", handle=handle, process_id=pid, **changed)
                    raise AssertionError("Stale native identity was accepted")
                except ValueError:
                    pass
            report["stale_identity"] = "replaced process and window rejected"
            editor_ref = desktop.find_element(handle, pid, "Document", exact=True)["matches"][0]["element_ref"]
            typed = desktop.type_text(editor_ref, "Local smoke document")
            assert typed["verified"], typed
            save_ref = desktop.find_element(handle, pid, "Save", exact=True)["matches"][0]["element_ref"]
            clicked = desktop.click_element(save_ref)
            assert clicked["method"] == "InvokePattern", clicked
            assert desktop.verify_state(handle, pid, "Saved smoke document")["verified"]
            report["control_actions"] = "ValuePattern text verified; InvokePattern observed through changed control state"
            try:
                vision.capture("window", handle=handle, process_id=pid)
                raise AssertionError("Protected window was captured")
            except PermissionError:
                pass
            bridge.capture_ready.emit()
            assert ready.wait(5)
            capture = vision.capture("window", handle=handle, process_id=pid)
            assert capture["isolated_window"] and capture["capture_method"] == "PrintWindow"
            recognized = vision.read_text(capture["id"])
            assert "JARVIX LOCAL OCR TEST" in recognized["text"].upper(), recognized
            assert "OTHER WINDOW CONTENT" not in recognized["text"].upper(), recognized
            report["window_capture"] = "isolated PrintWindow verified beneath another owned window"
            report["ocr"] = {"source": recognized["source"], "language": recognized["language"],
                             "test_text_found": True, "external_upload": recognized["external_upload"]}
            report["success"] = True
    except BaseException as exc:
        report["success"] = False
        report["error"] = str(exc)
        traceback.print_exc()
    finally:
        bridge.finished.emit()


QTimer.singleShot(300, lambda: threading.Thread(target=verify, daemon=False).start())
QTimer.singleShot(90000, app.quit)
app.exec()
window.close()
cover.close()
print(json.dumps(report, indent=2))
sys.exit(0 if report.get("success") else 1)
