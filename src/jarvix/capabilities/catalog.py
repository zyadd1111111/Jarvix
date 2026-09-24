"""Load tool families on demand to keep provider requests small and bounded."""
from jarvix.capabilities.schema import array, enum, register
from jarvix.domain import ToolResult

BASE_TOOLS = frozenset({"capabilities.load", "notes.search", "notes.create", "tasks.list", "tasks.create",
    "memory.search", "memory.remember", "apps.list", "apps.open", "projects.list", "files.search",
    "files.read_text", "system.status", "web.open", "web.search", "calculate.evaluate"})


def initial_tools(specs):
    if len(specs) <= 64:
        return specs
    selected = [spec for spec in specs if spec.name in BASE_TOOLS]
    return selected if any(spec.name == "capabilities.load" for spec in selected) else specs[:64]


def setup(s, registry):
    groups = sorted({spec.name.split(".")[0] for spec in registry.specs()})

    def load(groups):
        enabled = set(s.enabled_tools())
        matches = [spec for spec in registry.specs()
                   if spec.name in enabled and spec.name.split(".")[0] in groups]
        return ToolResult(True, {"tools": [{"name": spec.name, "description": spec.description}
                                          for spec in matches],
                                 "message": "These tool schemas are available on the next planning step."},
                          sensitivity="public")

    register(registry, "capabilities.load",
             "Load more tools before using them. Choose families: " + ", ".join(groups) +
             ". Loading capabilities does not execute their actions or read private data.",
             {"groups": array(enum(*groups), 3)}, ("groups",), load)
