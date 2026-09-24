"""Framework-independent contracts shared by providers, tools and orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

Json = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Json


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    metadata: Json = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Json
    permission: str = "local.read"
    risk: Literal["read", "write", "external"] = "read"
    permission_level: int | None = None
    result_schema: Json | None = None


@dataclass
class Completion:
    message: Message
    usage: Json = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Safe user-facing provider error; must not contain tokens or request bodies."""


class AIProvider(Protocol):
    id: str
    def complete(self, messages: list[Message], tools: list[ToolSpec], model: str) -> Completion: ...


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    sensitivity: Literal["public", "local"] = "local"

    def as_dict(self) -> Json:
        return {"ok": self.ok, "data": self.data, "error": self.error}


@dataclass(frozen=True)
class PermissionRequest:
    kind: Literal["execute", "disclose"]
    tool_name: str
    permission: str
    description: str
    arguments: Json
    preview: str = ""


Approval = Callable[[PermissionRequest], bool]
EventSink = Callable[[str, Json], None]
