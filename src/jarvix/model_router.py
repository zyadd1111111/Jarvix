"""Explicit task-role model routing; availability and capabilities are supplied facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from jarvix.domain import ProviderError

MODEL_ROLES = frozenset({"chat", "planning", "summarization", "embeddings", "vision", "coding", "document_analysis"})
ROUTING_PROFILES = ("local_only", "fast", "balanced", "quality", "custom")


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    capabilities: frozenset[str] = frozenset()
    context_length: int | None = None
    is_local: bool = False
    available: bool = True
    cost: float | None = None
    healthy: bool = True
    failure_count: int = 0
    latency_ms: float | None = None

    def __post_init__(self):
        if (any(type(value) is not bool for value in (self.available, self.is_local, self.healthy))
                or type(self.failure_count) is not int or self.failure_count < 0
                or any(value is not None and (type(value) not in {int, float}
                    or not math.isfinite(value) or value < 0) for value in (self.cost, self.latency_ms))):
            raise ValueError("Model cost, latency and failure counts must be finite and nonnegative.")

    def as_dict(self):
        value = asdict(self)
        value["capabilities"] = sorted(self.capabilities)
        return value


class ModelRouter:
    def __init__(self, candidates: list[ModelCandidate], roles: dict | None = None, fallbacks: dict | None = None):
        if len(candidates) > 512 or len({(item.provider, item.model) for item in candidates}) != len(candidates):
            raise ValueError("Model candidates must be unique and limited to 512.")
        self.candidates = tuple(candidates)
        self.roles = dict(roles or {})
        self.fallbacks = dict(fallbacks or {})
        if (set(self.roles) | set(self.fallbacks)) - MODEL_ROLES:
            raise ValueError("Unknown model-routing role.")
        for role, choice in self.roles.items():
            self.roles[role] = self._identity(choice)
        for role, choices in self.fallbacks.items():
            if not isinstance(choices, (tuple, list)) or len(choices) > 8:
                raise ValueError("A fallback chain must contain at most eight model choices.")
            identities = tuple(self._identity(choice) for choice in choices)
            if len(set(identities)) != len(identities):
                raise ValueError("Fallback model choices must be unique.")
            self.fallbacks[role] = identities

    @staticmethod
    def _identity(choice):
        if isinstance(choice, dict):
            choice = (choice.get("provider"), choice.get("model"))
        if (not isinstance(choice, (tuple, list)) or len(choice) != 2
                or any(not isinstance(value, str) or not value for value in choice)):
            raise ValueError("A model choice must contain a provider and model.")
        return tuple(choice)

    def select(self, task="chat", **requirements) -> ModelCandidate:
        return self.chain(task, **requirements)[0]

    def chain(self, task="chat", *, require_tools=False, require_vision=False,
               context_tokens=0, local_only=False, prefer_local=False,
               prefer_cloud=False, cost_preference="balanced", latency_preference="balanced",
               override=None, profile="balanced") -> tuple[ModelCandidate, ...]:
        if (task not in MODEL_ROLES or type(context_tokens) is not int or context_tokens < 0
                or cost_preference not in {"balanced", "low"}
                or latency_preference not in {"balanced", "low"} or profile not in ROUTING_PROFILES
                or (prefer_local and prefer_cloud)
                or any(type(value) is not bool for value in (require_tools, require_vision, local_only,
                                                           prefer_local, prefer_cloud))):
            raise ValueError("Invalid model-routing request.")
        local_only = local_only or profile == "local_only"
        if profile == "fast":
            latency_preference = "low"
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
            if not candidate.healthy:
                return "temporarily unhealthy after provider failures"
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
            return (candidate,)

        eligible = [candidate for candidate in self.candidates if rejection(candidate) is None]
        configured = self.roles.get(task)
        eligible.sort(key=lambda item: (
            item.failure_count > 0,
            (item.provider, item.model) != configured,
            0 if (prefer_local and item.is_local) or (prefer_cloud and not item.is_local) else 1,
            item.failure_count,
            -(item.context_length or 0) if profile == "quality" else 0,
            (item.latency_ms if item.latency_ms is not None else float("inf")) if latency_preference == "low" else 0,
            (item.cost if item.cost is not None else float("inf")) if cost_preference == "low" else 0,
            item.latency_ms if item.latency_ms is not None else float("inf"),
        ))
        if task in self.fallbacks:
            if configured is None and eligible:
                configured = (eligible[0].provider, eligible[0].model)
            order = ([configured] if configured else []) + list(self.fallbacks[task])
            eligible = [item for identity in dict.fromkeys(order) for item in eligible
                        if (item.provider, item.model) == identity]
        if not eligible:
            raise ProviderError("No available configured model meets this request's capabilities, context and privacy requirements.")
        # Stable configuration order breaks ties; unknown cost is never called free.
        return tuple(eligible)

    def fallback(self, current, task="chat", *, attempted=(), cloud_disclosure_approved=False,
                 **requirements) -> ModelCandidate:
        """Choose a compatible fallback; a local-to-cloud transition always needs consent."""
        current = self._identity(current)
        previous = next((item for item in self.candidates if (item.provider, item.model) == current), None)
        if previous is None:
            raise ProviderError("The failed provider/model is not configured or discovered.")
        excluded = {current, *(self._identity(choice) for choice in attempted)}
        eligible = [item for item in self.chain(task, **requirements)
                    if (item.provider, item.model) not in excluded]
        if previous.is_local:
            # Try compatible local models before requesting any cloud disclosure.
            eligible.sort(key=lambda item: not item.is_local)
        if not eligible:
            raise ProviderError("No compatible fallback remains for this request.")
        candidate = eligible[0]
        if previous.is_local and not candidate.is_local and cloud_disclosure_approved is not True:
            raise PermissionError("Local-to-cloud fallback requires fresh disclosure approval.")
        return candidate
