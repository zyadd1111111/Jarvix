"""Offline, disposable-profile timings; never inspect the user's active profile."""
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jarvix.services import Services


def main():
    timings = {}
    def measure(name, action):
        start = time.perf_counter()
        value = action()
        timings[name] = round((time.perf_counter() - start) * 1000, 2)
        return value
    with tempfile.TemporaryDirectory(prefix="jarvix-profile-") as folder:
        s = measure("services_startup_ms", lambda: Services(Path(folder) / "profile"))
        try:
            for index in range(100):
                s.save_note(f"Note {index}", f"Jarvix OAuth browser control topic {index}.")
            measure("database_read_100_notes_ms", s.list_notes)
            first = measure("index_100_notes_ms", lambda: s.search.rebuild(kinds=["note"]))
            second = measure("index_unchanged_ms", lambda: s.search.rebuild(kinds=["note"]))
            measure("keyword_query_ms", lambda: s.search.query("OAuth browser"))
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QCoreApplication, QEvent
            from jarvix.ui.window import MainWindow
            app = QApplication.instance() or QApplication([])
            app.setQuitOnLastWindowClosed(False)
            window = measure("ui_shell_init_ms", lambda: MainWindow(s))
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            print(json.dumps({"timings": timings, "first_index": first, "unchanged_index": second,
                "external_providers": "Not contacted. Live embeddings/streaming and browser-peer latency depend on configured endpoints.",
                "profile": "Disposable synthetic local data"}, indent=2))
        finally:
            s.close()


if __name__ == "__main__":
    main()
