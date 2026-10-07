"""Bounded fallback before the first response token; never retries local actions."""
from __future__ import annotations

from dataclasses import asdict
import json
from time import monotonic

from jarvix.attachments import context_message
from jarvix.domain import ProviderError
from jarvix.runtime import check_cancelled


class RoutedProvider:
    def __init__(self, services, choices, approve, on_event, *, cloud_requires_approval=False):
        if not choices:
            raise ProviderError("No compatible model is available.")
        self.s, self.approve, self.on_event = services, approve, on_event
        # ponytail: four candidates per request; avoids provider retry storms.
        self.choices = list(choices[:4])
        self.index, self.backend = 0, None
        self.cloud_requires_approval = cloud_requires_approval
        self._announced = False

    @property
    def current(self):
        return self.choices[self.index]

    @property
    def id(self):
        return self.current["provider"]

    @property
    def current_model(self):
        return self.current["model"]

    @property
    def is_local(self):
        return self.current["is_local"]

    @property
    def supports_vision(self):
        return "vision" in self.current["capabilities"]

    def close(self):
        if self.backend is not None:
            close = getattr(self.backend, "close", None)
            if callable(close):
                close()
            self.backend = None

    def complete(self, messages, tools, model):
        return self.stream(messages, tools, model, lambda text: None)

    def stream(self, messages, tools, model, on_delta):
        context = {"messages": [context_message(message) for message in messages],
                   "tools": [asdict(spec) for spec in tools]}
        # Recompute after tool follow-ups; the initially routed history can grow.
        context_tokens = len(json.dumps(context, ensure_ascii=False).encode("utf-8")) // 3 + 4096
        context_tokens += 4096 * sum(len(message.images) for message in messages)
        requires_tools = any(message.tool_calls or message.role == "tool" for message in messages)
        requires_vision = any(message.images for message in messages)
        while True:
            check_cancelled()
            if not self.is_local and self.s.routing_options()["local_only"]:
                raise ProviderError("Local-only mode blocks this cloud request.")
            capacity = self.current.get("context_length")
            incompatible = capacity is not None and (type(capacity) is not int or capacity < context_tokens)
            if self.index > 0:
                incompatible |= (capacity is None or (requires_tools and "tools" not in self.current["capabilities"])
                                 or (requires_vision and not self.supports_vision))
            if incompatible:
                if not self._advance():
                    raise ProviderError("No model supports the current conversation's context and capabilities.")
                continue
            if not self.is_local and self.cloud_requires_approval:
                try:
                    self.s.approve_model_fallback(self.id, self.current_model, messages, self.approve)
                except PermissionError as exc:
                    raise ProviderError(str(exc)) from None
                self.cloud_requires_approval = False
            if not self._announced:
                self.on_event("model_selected", {"provider": self.id, "model": self.current_model,
                              "local": self.is_local, "fallback": self.index > 0})
                self._announced = True
            self.s.latest_context = {"provider": self.id, "model": self.current_model, **context}
            started, emitted = monotonic(), False
            def delta(text):
                nonlocal emitted
                check_cancelled()
                emitted = True
                on_delta(text)
            try:
                if self.backend is None:
                    self.backend = self.s.make_provider(self.id)
                if callable(getattr(self.backend, "stream", None)):
                    result = self.backend.stream(messages, tools, self.current_model, delta)
                else:
                    result = self.backend.complete(messages, tools, self.current_model)
            except ProviderError:
                self._outcome(False, started)
                # A visible partial response must never be replaced or duplicated.
                if emitted or not self._advance():
                    raise
                continue
            self._outcome(True, started)
            return result

    def _advance(self):
        if self.index + 1 == len(self.choices):
            return False
        previous_local = self.is_local
        self.close()
        if previous_local:
            self.choices[self.index + 1:] = sorted(self.choices[self.index + 1:],
                                                  key=lambda candidate: not candidate["is_local"])
        self.index += 1
        self.cloud_requires_approval = not self.is_local
        self._announced = False
        return True

    def _outcome(self, success, started):
        try:
            self.s.record_model_outcome(self.id, self.current_model, success,
                                        min((monotonic() - started) * 1000, 86_400_000))
        except Exception:
            # Receipt storage must not repeat a completed provider/tool request.
            self.on_event("model_health", {"status": "unavailable"})
