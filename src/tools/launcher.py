from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
import webbrowser
from urllib.parse import urlparse


class LauncherError(RuntimeError):
    """Raised when an app or website launch is not allowed or fails."""


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    message: str


class Launcher:
    """Launches user-approved apps and websites."""

    def open_app(self, name: str) -> LaunchResult:
        app_name = name.strip()
        if not app_name:
            raise LauncherError("Enter an app name first.")
        if platform.system() != "Darwin":
            raise LauncherError("Jarvix v1 app launching is macOS-only.")
        result = subprocess.run(
            ["open", "-a", app_name],
            shell=False,
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Unable to open app.").strip()
            raise LauncherError(message)
        return LaunchResult(True, f"Opened {app_name}.")

    def open_website(self, url: str) -> LaunchResult:
        safe_url = self.normalize_url(url)
        opened = webbrowser.open(safe_url, new=2)
        if not opened:
            raise LauncherError("Browser did not accept the website request.")
        return LaunchResult(True, f"Opened {safe_url}.")

    @staticmethod
    def normalize_url(url: str) -> str:
        clean_url = url.strip()
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"}:
            raise LauncherError("Only http:// and https:// websites are allowed.")
        if not parsed.netloc:
            raise LauncherError("Website URL must include a host.")
        return clean_url

