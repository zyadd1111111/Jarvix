from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

from ai.client import AIClient, AIClientError, ChatMessage
from config.settings import AppSettings


def test_openai_client_reports_missing_key() -> None:
    settings = AppSettings(
        data_dir=Path("data"),
        database_path=Path("data/jarvix.db"),
        ai_provider="openai",
        openai_api_key=None,
        gemini_api_key=None,
        model="gpt-5.5",
    )
    client = AIClient(settings)

    with pytest.raises(AIClientError, match="OPENAI_API_KEY"):
        client.generate_reply([ChatMessage("user", "hello")])


def test_gemini_client_reports_missing_key() -> None:
    settings = AppSettings(
        data_dir=Path("data"),
        database_path=Path("data/jarvix.db"),
        ai_provider="gemini",
        openai_api_key=None,
        gemini_api_key=None,
        model="gemini-2.5-flash",
    )
    client = AIClient(settings)

    with pytest.raises(AIClientError, match="GEMINI_API_KEY"):
        client.generate_reply([ChatMessage("user", "hello")])


def test_gemini_client_uses_google_genai_sdk(monkeypatch) -> None:
    class FakeModels:
        def create(self, model, input):
            assert model == "gemini-3.5-flash"
            assert "System:" in input
            assert "User: hello" in input
            return type("Response", (), {"output_text": "hi from gemini"})()

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "AQ.FakeGeminiAuthKeyForLocalTest"
            self.interactions = FakeModels()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_google = ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    settings = AppSettings(
        data_dir=Path("data"),
        database_path=Path("data/jarvix.db"),
        ai_provider="gemini",
        openai_api_key=None,
        gemini_api_key="AQ.FakeGeminiAuthKeyForLocalTest",
        model="gemini-3.5-flash",
    )

    assert AIClient(settings).generate_reply([ChatMessage("user", "hello")]) == "hi from gemini"


def test_gemini_client_accepts_legacy_aiza_key_shape(monkeypatch) -> None:
    class FakeInteractions:
        def create(self, model, input):
            return type("Response", (), {"output_text": "hi from legacy key"})()

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "AIzaFakeGeminiKeyForLocalTest"
            self.interactions = FakeInteractions()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_google = ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    settings = AppSettings(
        data_dir=Path("data"),
        database_path=Path("data/jarvix.db"),
        ai_provider="gemini",
        openai_api_key=None,
        gemini_api_key="AIzaFakeGeminiKeyForLocalTest",
        model="gemini-3.5-flash",
    )

    assert AIClient(settings).generate_reply([ChatMessage("user", "hello")]) == "hi from legacy key"
