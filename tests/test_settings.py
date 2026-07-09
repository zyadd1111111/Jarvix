from __future__ import annotations

from config.settings import AppSettings, DEFAULT_GEMINI_MODEL, DEFAULT_OPENAI_MODEL


def test_settings_read_environment_without_persisting_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("JARVIX_MODEL", "gpt-test")

    settings = AppSettings.from_environment(tmp_path)

    assert settings.has_openai_key
    assert settings.openai_api_key == "test-key"
    assert settings.ai_provider == "openai"
    assert settings.model == "gpt-test"
    assert settings.database_path == tmp_path.resolve() / "jarvix.db"


def test_settings_default_openai_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("JARVIX_MODEL", raising=False)
    monkeypatch.delenv("JARVIX_AI_PROVIDER", raising=False)

    settings = AppSettings.from_environment(tmp_path)

    assert not settings.has_openai_key
    assert settings.ai_provider == "openai"
    assert settings.model == DEFAULT_OPENAI_MODEL


def test_settings_gemini_provider_and_default_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIX_MODEL", raising=False)
    monkeypatch.setenv("JARVIX_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.FakeGeminiAuthKeyForLocalTest")

    settings = AppSettings.from_environment(tmp_path)

    assert settings.ai_provider == "gemini"
    assert settings.has_gemini_key
    assert settings.has_ai_key
    assert settings.model == DEFAULT_GEMINI_MODEL


def test_settings_auto_selects_gemini_when_only_gemini_key_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIX_AI_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIX_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")

    settings = AppSettings.from_environment(tmp_path)

    assert settings.ai_provider == "gemini"
    assert settings.gemini_api_key == "not-a-real-key"
    assert settings.active_key_warning


def test_settings_accepts_google_ai_studio_key_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIX_MODEL", raising=False)
    monkeypatch.setenv("JARVIX_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyFakeGeminiKeyForLocalTest")

    settings = AppSettings.from_environment(tmp_path)

    assert settings.has_ai_key
    assert settings.active_key_warning is None


def test_settings_accepts_new_gemini_auth_key_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIX_MODEL", raising=False)
    monkeypatch.setenv("JARVIX_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.FakeGeminiAuthKeyForLocalTest")

    settings = AppSettings.from_environment(tmp_path)

    assert settings.has_ai_key
    assert settings.active_key_warning is None
