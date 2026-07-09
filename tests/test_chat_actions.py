from __future__ import annotations

from tools.chat_actions import ChatActionRouter


def test_routes_calculation() -> None:
    action = ChatActionRouter().plan("calculate 6 * 7")

    assert action is not None
    assert action.kind == "calculation"
    assert action.response == "6 * 7 = 42"


def test_routes_common_website_alias() -> None:
    action = ChatActionRouter().plan("open a new tab for youtube")

    assert action is not None
    assert action.kind == "open_website"
    assert action.target == "https://www.youtube.com"
    assert action.confirmation


def test_routes_polite_voice_style_website_request() -> None:
    action = ChatActionRouter().plan("can you open up youtube")

    assert action is not None
    assert action.kind == "open_website"
    assert action.target == "https://www.youtube.com"


def test_routes_domain_to_https_website() -> None:
    action = ChatActionRouter().plan("visit example.com/path")

    assert action is not None
    assert action.kind == "open_website"
    assert action.target == "https://example.com/path"


def test_routes_app_alias() -> None:
    action = ChatActionRouter().plan("open calculator")

    assert action is not None
    assert action.kind == "open_app"
    assert action.target == "Calculator"


def test_routes_explicit_app_name() -> None:
    action = ChatActionRouter().plan("launch app Visual Studio Code")

    assert action is not None
    assert action.kind == "open_app"
    assert action.target == "Visual Studio Code"


def test_ordinary_chat_falls_through_to_ai() -> None:
    assert ChatActionRouter().plan("tell me a story about a desktop assistant") is None
