"""Verify native packaged startup of OLD, NEW and NEW Settings in isolated profiles."""
import json
import os
from pathlib import Path
import subprocess
import uuid

from jarvix.services import Services
from review_ui import ReviewVault


def main():
    root = Path(__file__).resolve().parents[1]
    binary = root / "dist/Jarvix/Jarvix.exe"
    output = root / "artifacts/liquid-packaged"
    output.mkdir(exist_ok=True)
    results = []
    cases = [("old", "cream", "Home")] + [("new", theme, page)
        for theme in ("cream", "frost", "graphite", "sand") for page in ("Home", "Chat", "Settings")]
    for mode, theme, page in cases:
        profile = output / ("profile-" + uuid.uuid4().hex)
        services = Services(profile, vault=ReviewVault())
        services.settings.set("ui.interface", mode)
        services.settings.set("liquid.theme", theme)
        services.settings.set("liquid.accent", "theme")
        services.settings.set("tray.enabled", False)
        services.close()
        image = output / (mode + "-" + theme + "-" + page.lower() + ".png")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.run([str(binary), "--data-dir", str(profile), "--page", page,
            "--screenshot", str(image)], cwd=root, env={**os.environ, "QT_QPA_PLATFORM": "windows"},
            startupinfo=startup, capture_output=True, timeout=45, check=False)
        if process.returncode != 0 or not image.exists():
            raise RuntimeError(f"Packaged {mode} {page} failed ({process.returncode}): " + process.stderr.decode(errors="replace")[-1500:])
        results.append({"interface": mode, "theme": theme, "page": page, "platform": "windows", "exit_code": process.returncode,
                        "image": str(image), "profile": str(profile)})
        print(f"Native packaged {mode} {theme} {page}: verified", flush=True)
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
