"""Render NEW with disposable offline data; assert OLD is unchanged after switching."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import uuid
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

import review_nexus as review
from jarvix.services import Services
from jarvix.domain import ToolResult
from jarvix.ui import pages, window
from jarvix.ui.theme import configure_fonts
from review_ui import ReviewVault


def old_sources():
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in Path("src/jarvix/ui").rglob("*.py")
            if "liquid" not in path.parts and path.name != "interface.py"}


def seed_skill(services):
    routine = services.workflows.save("Review tasks", [
        {"kind": "action", "tool": "tasks.list", "arguments": {}}], kind="routine", enabled=False)
    args = {"name": "Review tasks", "routine_id": routine["id"],
            "description": "Review unfinished local tasks before starting work."}
    preview = services.execute_tool("skills.learn_preview", args)
    assert preview.ok, preview.error
    saved = services.execute_tool("skills.save", {**args,
        "review_fingerprint": preview.data["review_fingerprint"]}, approve=lambda request: request.tool_name == "skills.save")
    assert saved.ok and saved.data["actions_executed"] == 0, saved.error


def contact_sheets(output, names):
    app = QApplication.instance() or QApplication([])
    configure_fonts()
    app.setFont(QFont("Segoe UI", 10))
    for start in range(0, len(names), 6):
        selected = names[start:start + 6]
        sheet = QImage(1960, ((len(selected) + 1) // 2) * 300, QImage.Format.Format_RGB32)
        sheet.fill(QColor("#e9e6de"))
        painter = QPainter(sheet)
        painter.setFont(QFont("Segoe UI", 10))
        painter.setPen(QColor("#293330"))
        for index, name in enumerate(selected):
            for side, (caption, folder) in enumerate((("OLD", output / "old-before"), ("NEW", output))):
                image = QImage(str(folder / (name + ".png")))
                assert not image.isNull(), "Missing screenshot: " + name
                x, y = 10 + (index % 2 * 2 + side) * 490, index // 2 * 300
                painter.drawText(x, y + 20, name.replace("-", " ").title() + " · " + caption)
                painter.drawImage(x, y + 26, image.scaled(480, 270, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
        painter.end()
        assert sheet.save(str(output / f"comparison-sheet-{start // 6 + 1}.png"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--theme", choices=("cream", "frost", "graphite", "sand"))
    parser.add_argument("--compare-all", action="store_true", help="Capture and compare every requested OLD page using the same disposable fixture.")
    parser.add_argument("--capture", choices=("legacy", "new"), help=argparse.SUPPRESS)
    parser.add_argument("--profile", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--conversation", help=argparse.SUPPRESS)
    parser.add_argument("--compare", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pages", nargs="+", choices=("Home", "Chat", "Operator", "Missions", "Knowledge",
        "Files", "Automations", "Skills", "Apps", "Integrations", "System", "Settings", "Projects",
        "Tasks", "Notes", "Memory", "Voice", "Activity"))
    args = parser.parse_args()
    if args.matrix:
        for width, height in ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440), (3840, 2160)):
            for scale in ("1", "1.25", "1.5", "2"):
                command = [sys.executable, __file__, "--width", str(width), "--height", str(height)]
                if args.empty:
                    command.append("--empty")
                if args.pages:
                    command.extend(["--pages", *args.pages])
                if args.compare_all:
                    command.append("--compare-all")
                if args.theme:
                    command.extend(["--theme", args.theme])
                subprocess.run(command, env={**os.environ, "QT_SCALE_FACTOR": scale}, check=True)
        return
    if args.native:
        os.environ["QT_QPA_PLATFORM"] = "windows"
    review.PAGES = tuple(args.pages) if args.pages else ("Home", "Chat", "Operator", "Missions", "Knowledge", "Files", "Automations",
                    "Skills", "Apps", "Integrations", "System", "Settings", "Projects", "Tasks",
                    "Notes", "Memory", "Voice", "Activity")
    if args.capture:
        app = QApplication([])
        app.setFont(QFont("Segoe UI Variable", 10))
        app.setCursorFlashTime(0)
        app.setQuitOnLastWindowClosed(False)
        snapshot = json.loads((args.profile / "review-snapshot.json").read_text(encoding="utf-8"))
        if not args.compare_all:
            review.capture(app, args.profile, args.capture, args.output, (args.width, args.height),
                           args.conversation, snapshot, compare=args.compare)
            return
        clock = datetime.fromisoformat(snapshot.pop("review_time"))
        devices = snapshot.pop("review_devices")
        class ReviewTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)
        execute = Services.execute_tool
        def fixed_devices(service, name, arguments, *positional, **keywords):
            if name in devices:
                return ToolResult(**devices[name])
            return execute(service, name, arguments, *positional, **keywords)
        fixed_iso = clock.astimezone().isoformat(timespec="seconds")
        with patch.object(window, "datetime", ReviewTime), patch.object(pages, "datetime", ReviewTime), \
                patch("jarvix.storage.now_iso", lambda: fixed_iso), \
                patch("jarvix.services.now_iso", lambda: fixed_iso), \
                patch.object(Services, "execute_tool", fixed_devices):
            review.capture(app, args.profile, args.capture, args.output, (args.width, args.height),
                           args.conversation, snapshot, compare=args.compare, compare_all=args.compare)
        return
    output = Path("artifacts/liquid") / (f"{args.width}x{args.height}-scale{os.environ.get('QT_SCALE_FACTOR', '1')}"
                                       + ("-empty" if args.empty else "") + ("-native" if args.native else "")
                                       + ("-nexus-" + args.theme if args.theme else ""))
    output.mkdir(parents=True, exist_ok=True)
    profile = output / ("profile-" + uuid.uuid4().hex)
    services = Services(profile, vault=ReviewVault())
    if args.theme:
        services.settings.set("liquid.theme", args.theme)
        services.settings.set("liquid.accent", "theme")
    conversation = None if args.empty else review.seed(services, profile)
    if not args.empty:
        seed_skill(services)
    snapshot = services.system_snapshot()
    if args.compare_all:
        snapshot["review_time"] = datetime.now().isoformat()
        snapshot["review_devices"] = {}
        for tool in ("system.gpu", "system.battery", "system.storage", "system.network"):
            result = services.execute_tool(tool, {})
            snapshot["review_devices"][tool] = {"ok": result.ok, "data": result.data, "error": result.error}
        services.db.backup(profile / "review-fixture.db")
    services.close()
    (profile / "review-snapshot.json").write_text(json.dumps(snapshot, default=str), encoding="utf-8")
    before_path, after_path = output / "old-before", output / "old-after"
    for path in (before_path, after_path):
        path.mkdir(exist_ok=True)
    sources = old_sources()
    # Interface changes apply on restart. Use the same process boundary here;
    # retaining several complete Qt applications misrepresents that lifecycle.
    for mode, target, compare in (("legacy", before_path, True), ("new", output, False), ("legacy", after_path, True)):
        if args.compare_all:
            # Restore only this freshly created review profile, so audit reads
            # and view selection from one capture cannot change the next fixture.
            with sqlite3.connect(profile / "review-fixture.db") as source, sqlite3.connect(profile / "jarvix.db") as destination:
                source.backup(destination)
        command = [sys.executable, "-X", "faulthandler", __file__, "--capture", mode, "--profile", str(profile),
                   "--output", str(target), "--width", str(args.width), "--height", str(args.height)]
        if conversation:
            command.extend(["--conversation", conversation])
        if compare:
            command.append("--compare")
        if args.pages:
            command.extend(["--pages", *args.pages])
        if args.native:
            command.append("--native")
        if args.compare_all:
            command.append("--compare-all")
        subprocess.run(command, check=True)
    assert old_sources() == sources, "OLD source changed during review"
    names = [page.lower() for page in review.PAGES] if args.compare_all else ["chat"]
    if args.compare_all and "settings" in names:
        names.append("settings-appearance")
    for name in names:
        before, after = (QImage(str(path / (name + ".png"))) for path in (before_path, after_path))
        first = before.convertToFormat(QImage.Format.Format_RGBA8888)
        second = after.convertToFormat(QImage.Format.Format_RGBA8888)
        assert not before.isNull() and before.size() == after.size() and first.constBits().tobytes() == second.constBits().tobytes(), "OLD changed: " + name
    if args.compare_all:
        (output / "comparison.json").write_text(json.dumps({"old_sources_unchanged": len(sources),
            "old_pixels_identical": names, "fixture_reset_between_captures": True,
            "clock_and_locally_measured_devices_frozen": True}, indent=2), encoding="utf-8")
        contact_sheets(output, names)
    print(f"OLD before/after pixels match exactly ({len(names)} screens).", flush=True)


if __name__ == "__main__":
    main()
