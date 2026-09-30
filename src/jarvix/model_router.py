"""Explicit task-role model routing; availability and capabilities are supplied facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from jarvix.domain import ProviderError

MODEL_ROLES = frozenset({"chat", "planning", "summarization", "embeddings", "vision"})


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    capabilities: frozenset[str] = frozenset()
    context_length: int | None = None
    is_local: bool = False
    available: bool = True
    cost: float | None = None

    def as_dict(self):
        value = asdict(self)
        value["capabilities"] = sorted(self.capabilities)
        return value


class ModelRouter:
    def __init__(self, candidates: list[ModelCandidate], roles: dict | None = None):
        if len(candidates) > 512 or len({(item.provider, item.model) for item in candidates}) != len(candidates):
            raise ValueError("Model candidates must be unique and limited to 512.")
        self.candidates = tuple(candidates)
        self.roles = dict(roles or {})
        if set(self.roles) - MODEL_ROLES:
            raise ValueError("Unknown model-routing role.")
        for role, choice in self.roles.items():
            self.roles[role] = self._identity(choice)

    @staticmethod
    def _identity(choice):
        if isinstance(choice, dict):
            choice = (choice.get("provider"), choice.get("model"))
        if (not isinstance(choice, (tuple, list)) or len(choice) != 2
                or any(not isinstance(value, str) or not value for value in choice)):
            raise ValueError("A model choice must contain a provider and model.")
        return tuple(choice)

    def select(self, task="chat", *, require_tools=False, require_vision=False,
               context_tokens=0, local_only=False, prefer_local=False,
               cost_preference="balanced", override=None) -> ModelCandidate:
        if (task not in MODEL_ROLES or type(context_tokens) is not int or context_tokens < 0
                or cost_preference not in {"balanced", "low"}):
            raise ValueError("Invalid model-routing request.")
        required = set()
        if task == "embeddings":
            required.add("embeddings")
        else:
            required.add("completion")
        if require_tools or task == "planning":
            required.add("tools")
        if require_vision or task == "vision":
            required.add("vision")

        def rejection(candidate):
            if not candidate.available:
                return "unavailable"
            if local_only and not candidate.is_local:
                return "blocked by local-only mode"
            if not required <= candidate.capabilities:
                return "missing required capabilities"
            if context_tokens and (candidate.context_length is None or candidate.context_length < context_tokens):
                return "insufficient or unreported context capacity"
            return None

        if override is not None:
            identity = self._identity(override)
            candidate = next((item for item in self.candidates if (item.provider, item.model) == identity), None)
            if candidate is None:
                raise ProviderError("The overridden provider/model is not configured or discovered.")
            reason = rejection(candidate)
            if reason:
                raise ProviderError(f"The overridden provider/model is {reason}. Change the model or request requirements.")
            return candidate

        eligible = [candidate for candidate in self.candidates if rejection(candidate) is None]
        if not eligible:
            raise ProviderError("No available configured model meets this request's capabilities, context and privacy requirements.")
        configured = self.roles.get(task)
        role_match = next((item for item in eligible if (item.provider, item.model) == configured), None)
        if role_match is not None:
            return role_match
        # Stable configuration order breaks ties; unknown cost is never called free.
        return min(eligible, key=lambda item: (
            0 if prefer_local and item.is_local else 1,
            (item.cost if item.cost is not None else float("inf")) if cost_preference == "low" else 0,
        ))
