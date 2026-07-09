from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(
            "PySide6 is required to launch Jarvix. "
            'Install dependencies with: python -m pip install -e ".[dev]"',
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 1

    from ai.client import AIClient
    from config.settings import AppSettings
    from memory.notes_store import NotesStore
    from tools.launcher import Launcher
    from tools.system_info import SystemInfoService
    from ui.main_window import JarvixWindow
    from voice.speech_service import SpeechService
    from voice.transcriber import VoiceTranscriber

    settings = AppSettings.from_environment(data_dir=data_dir)
    app = QApplication(sys.argv)
    window = JarvixWindow(
        settings=settings,
        ai_client=AIClient(settings=settings),
        notes_store=NotesStore(settings.database_path),
        transcriber=VoiceTranscriber(),
        speech_service=SpeechService(),
        launcher=Launcher(),
        system_info=SystemInfoService(),
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
