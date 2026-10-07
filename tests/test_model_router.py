import pytest

from jarvix.domain import ProviderError
from jarvix.model_router import ModelCandidate, ModelRouter


def choices():
    return [ModelCandidate("ollama", "local", frozenset({"completion", "embeddings"}), 8192, True, cost=0),
            ModelCandidate("openai", "planner", frozenset({"completion", "tools", "vision"}), 128000, cost=1),
            ModelCandidate("gemini", "offline", frozenset({"tools", "vision"}), 128000, available=False)]


def test_role_selection_is_explicit_and_privacy_and_capability_requirements_win():
    router = ModelRouter(choices(), {"planning": {"provider": "openai", "model": "planner"},
                                    "summarization": ["ollama", "local"]})
    assert router.select("planning", require_tools=True).model == "planner"
    assert router.select("summarization").is_local is True
    assert router.select("embeddings", local_only=True).model == "local"
    with pytest.raises(ProviderError, match="No available"):
        router.select("planning", require_tools=True, local_only=True)


def test_explicit_override_never_falls_back_or_bypasses_constraints():
    router = ModelRouter(choices())
    assert router.select(override={"provider": "openai", "model": "planner"}).model == "planner"
    for override, requirement in [(("ollama", "local"), {"require_vision": True}),
                                  (("openai", "planner"), {"local_only": True}),
                                  (("gemini", "offline"), {}), (("unknown", "model"), {})]:
        with pytest.raises(ProviderError, match="overridden"):
            router.select(override=override, **requirement)


def test_context_capacity_is_checked_and_unreported_capacity_is_not_invented():
    router = ModelRouter(choices())
    assert router.select(context_tokens=9000).model == "planner"
    with pytest.raises(ProviderError, match="No available"):
        router.select(context_tokens=200000)
    router = ModelRouter([ModelCandidate("local", "unreported", is_local=True)])
    with pytest.raises(ProviderError, match="No available"):
        router.select()
    with pytest.raises(ProviderError, match="No available"):
        router.select(context_tokens=1)


def test_availability_local_preference_and_cost_are_respected():
    router = ModelRouter(list(reversed(choices())), {"chat": ("gemini", "offline")})
    assert router.select(prefer_local=True).model == "local"
    assert router.select(cost_preference="low").model == "local"
    assert router.select(require_vision=True).model == "planner"


def test_router_rejects_duplicate_candidates_and_invalid_role_configuration():
    candidate = choices()[0]
    with pytest.raises(ValueError, match="unique"):
        ModelRouter([candidate, candidate])
    with pytest.raises(ValueError, match="Unknown"):
        ModelRouter(choices(), {"dangerous": ("openai", "planner")})
    with pytest.raises(ValueError, match="provider and model"):
        ModelRouter(choices(), {"chat": {"provider": "openai"}})


def test_planning_requires_tools_and_embedding_models_never_route_to_chat():
    router = ModelRouter(choices())
    assert router.select("planning").model == "planner"
    router = ModelRouter([ModelCandidate("ollama", "embed", frozenset({"embeddings"}), is_local=True)])
    assert router.select("embeddings", local_only=True).model == "embed"
    for role in ("chat", "planning", "summarization", "vision"):
        with pytest.raises(ProviderError, match="No available"):
            router.select(role)


def test_coding_document_roles_and_observed_health_latency_and_failures():
    models = [ModelCandidate("local", "slow", frozenset({"completion"}), is_local=True, latency_ms=900),
              ModelCandidate("local", "fast", frozenset({"completion"}), is_local=True, latency_ms=40),
              ModelCandidate("openai", "failed", frozenset({"completion"}), failure_count=2, latency_ms=1),
              ModelCandidate("local", "unhealthy", frozenset({"completion"}), healthy=False, latency_ms=0)]
    router = ModelRouter(models)
    assert router.select("coding", prefer_local=True).model == "fast"
    assert router.select("document_analysis", latency_preference="low").model == "fast"
    assert router.select(prefer_cloud=True).model == "fast"  # Recent failures outweigh preference.
    with pytest.raises(ProviderError, match="unhealthy"):
        router.select(override=("local", "unhealthy"))
    for kwargs in ({"latency_ms": float("nan")}, {"cost": -1}, {"failure_count": True}):
        with pytest.raises(ValueError, match="finite"):
            ModelCandidate("local", "invalid", **kwargs)


def test_fallback_chain_keeps_capability_context_and_cloud_disclosure_guards():
    models = choices() + [ModelCandidate("local", "compatible", frozenset({"completion", "tools"}), 16000, True),
                          ModelCandidate("local", "small", frozenset({"completion", "tools"}), 1000, True)]
    router = ModelRouter(models, {"planning": ("ollama", "local")},
                         {"planning": [("local", "small"), ("local", "compatible"), ("openai", "planner")]})
    assert [item.model for item in router.chain("planning", context_tokens=9000)] == ["compatible", "planner"]
    assert router.fallback(("ollama", "local"), "planning", context_tokens=9000).is_local
    with pytest.raises(PermissionError, match="fresh disclosure"):
        router.fallback(("local", "compatible"), "planning", context_tokens=9000)
    assert router.fallback(("local", "compatible"), "planning", context_tokens=9000,
                           cloud_disclosure_approved=True).model == "planner"
    with pytest.raises(ProviderError):
        router.fallback(("local", "compatible"), "planning", context_tokens=9000, local_only=True,
                        cloud_disclosure_approved=True)
    with pytest.raises(ProviderError, match="No compatible"):
        router.fallback(("openai", "planner"), "planning", override=("openai", "planner"))
    automatic = ModelRouter(models, fallbacks={"chat": []})
    assert len(automatic.chain("chat", prefer_local=True)) == 1
    with pytest.raises(ProviderError, match="No compatible"):
        automatic.fallback(("ollama", "local"), "chat", prefer_local=True)
    for chain in [[("local", "same")] * 2, [("local", str(index)) for index in range(9)]]:
        with pytest.raises(ValueError):
            ModelRouter(models, fallbacks={"chat": chain})


def test_model_failure_cooldown_and_metrics_survive_restart(tmp_path):
    from jarvix.services import Services
    vault = type("NoVault", (), {"get": lambda self, _: None})()
    services = Services(tmp_path, vault=vault)
    services.records.put("provider_health", {"available": True, "models": [
        {"id": "primary", "capabilities": ["completion"]},
        {"id": "backup", "capabilities": ["completion"]}]}, "ollama")
    services.configure_model_role("coding", "ollama", "primary")
    services.configure_model_fallbacks("coding", [{"provider": "ollama", "model": "backup"}])
    for _ in range(3):
        services.record_model_outcome("ollama", "primary", False, 100)
    assert services.model_router().select("coding").model == "backup"
    services.close()
    services = Services(tmp_path, vault=vault)
    try:
        assert services.model_router().select("coding").model == "backup"
        state = services.model_health()["models"][0]
        assert state["failures"] == 3 and state["consecutive_failures"] == 3 and state["latency_ms"] == 100
        services.record_model_outcome("ollama", "primary", True, 50)
        assert services.model_router().select("coding").model == "primary"
        assert services.model_health()["models"][0]["latency_ms"] == 85
        with pytest.raises(ValueError):
            services.record_model_outcome("ollama", "primary", False, float("inf"))
    finally:
        services.close()


def test_cloud_fallback_approval_shows_exact_local_context_and_is_never_cached(tmp_path):
    from jarvix.domain import ImageAttachment, Message
    from jarvix.services import Services
    services = Services(tmp_path, vault=type("NoVault", (), {"get": lambda self, _: None})())
    image = ImageAttachment("private.png", "image/png", "YWJj", "digest", 1, 1)
    messages = [Message("user", "private local document text", images=[image])]
    requests = []
    try:
        with pytest.raises(PermissionError, match="denied"):
            services.approve_model_fallback("openai", "gpt-4.1-mini", messages,
                                            lambda request: requests.append(request) or False)
        assert requests[0].kind == "disclose" and "private local document text" in requests[0].preview
        assert requests[0].images == (image,)
        for _ in range(2):
            assert services.approve_model_fallback("openai", "gpt-4.1-mini", messages,
                lambda request: requests.append(request) or True)
        assert len(requests) == 3
        services.settings.set("ai.local_only", True)
        with pytest.raises(PermissionError, match="Local-only"):
            services.approve_model_fallback("openai", "gpt-4.1-mini", messages,
                                            lambda request: requests.append(request) or True)
        assert len(requests) == 3
    finally:
        services.close()
