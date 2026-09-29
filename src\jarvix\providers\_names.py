"""Provider-safe aliases without imposing vendor naming rules on the registry."""
from __future__ import annotations

import hashlib
import re

from jarvix.domain import ProviderError, ToolSpec


def wire_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 256:
        raise ProviderError("A registered tool has an invalid name.")
    # Always hash, including already-safe names, so a registry entry cannot
    # masquerade as the transformed name of another entry.
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:39]
    return f"j_{stem}_{hashlib.sha256(name.encode()).hexdigest()[:20]}"


def name_map(tools: list[ToolSpec]) -> dict[str, str]:
    names = {wire_name(tool.name): tool.name for tool in tools}
    if len(names) != len(tools):
        raise ProviderError("Registered tools must have unique provider identifiers.")
    return names


def domain_name(name: object, names: dict[str, str]) -> str:
    if not isinstance(name, str) or name not in names:
        raise ProviderError("The AI provider requested an unregistered tool. No tool was executed.")
    return names[name]
