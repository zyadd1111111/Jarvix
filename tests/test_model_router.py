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
