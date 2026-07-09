from __future__ import annotations

from dataclasses import dataclass
import re

from tools.calculator import CalculationError, SafeCalculator
from tools.launcher import Launcher, LauncherError


@dataclass(frozen=True)
class ChatAction:
    kind: str
    target: str
    response: str
    confirmation: str | None = None


WEBSITE_ALIASES = {
    "chatgpt": "https://chatgpt.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "youtube": "https://www.youtube.com",
}

APP_ALIASES = {
    "calculator": "Calculator",
    "calendar": "Calendar",
    "notes": "Notes",
    "safari": "Safari",
    "terminal": "Terminal",
}


class ChatActionRouter:
    """Turns simple chat requests into safe local actions."""

    def __init__(self, calculator: SafeCalculator | None = None) -> None:
        self.calculator = calculator or SafeCalculator()

    def plan(self, text: str) -> ChatAction | None:
        calculation = self._plan_calculation(text)
        if calculation is not None:
            return calculation
        launch = self._plan_launch(text)
        if launch is not None:
            return launch
        return None

    def _plan_calculation(self, text: str) -> ChatAction | None:
        try:
            result = self.calculator.try_calculate(text)
        except CalculationError as exc:
            return ChatAction(kind="calculation_error", target=text, response=str(exc))
        if result is None:
            return None
        return ChatAction(kind="calculation", target=result.expression, response=result.response)

    def _plan_launch(self, text: str) -> ChatAction | None:
        cleaned = text.strip().strip(".")
        cleaned = re.sub(
            r"^(?:jarvix[, ]+)?(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        match = re.match(
            r"^(?:open|launch|start|go to|visit)\s+(?:up\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not match:
            return None

        target = self._clean_launch_target(match.group(1))
        if not target:
            return None

        app_target = self._extract_prefixed_target(target, ("app", "application"))
        if app_target:
            app_name = APP_ALIASES.get(app_target.lower(), app_target)
            return ChatAction(
                kind="open_app",
                target=app_name,
                response=f"Opening {app_name}.",
                confirmation=f"Open app `{app_name}`?",
            )

        website_target = self._extract_prefixed_target(
            target, ("website", "site", "url", "tab", "browser tab")
        )
        if website_target:
            return self._website_action(website_target)

        lowered = target.lower()
        if lowered in WEBSITE_ALIASES:
            return self._website_action(lowered)
        if lowered in APP_ALIASES:
            app_name = APP_ALIASES[lowered]
            return ChatAction(
                kind="open_app",
                target=app_name,
                response=f"Opening {app_name}.",
                confirmation=f"Open app `{app_name}`?",
            )
        if self._looks_like_domain_or_url(target):
            return self._website_action(target)
        return None

    @staticmethod
    def _clean_launch_target(target: str) -> str:
        target = target.strip()
        target = re.sub(r"^(?:a\s+)?(?:new\s+)?(?:browser\s+)?tab\s+(?:for|to)\s+", "", target)
        target = re.sub(r"^(?:the\s+)", "", target)
        return target.strip()

    @staticmethod
    def _extract_prefixed_target(target: str, prefixes: tuple[str, ...]) -> str | None:
        for prefix in prefixes:
            match = re.match(rf"^{re.escape(prefix)}\s+(?:for\s+|to\s+)?(.+)$", target, re.I)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _looks_like_domain_or_url(target: str) -> bool:
        return target.startswith(("http://", "https://")) or bool(
            re.match(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/.*)?$", target)
        )

    def _website_action(self, target: str) -> ChatAction | None:
        try:
            url = self._normalize_chat_url(target)
        except LauncherError as exc:
            return ChatAction(kind="launch_error", target=target, response=str(exc))
        return ChatAction(
            kind="open_website",
            target=url,
            response=f"Opening {url}.",
            confirmation=f"Open website `{url}`?",
        )

    @staticmethod
    def _normalize_chat_url(target: str) -> str:
        lowered = target.lower().strip()
        url = WEBSITE_ALIASES.get(lowered, target.strip())
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return Launcher.normalize_url(url)
