"""Small schema helpers for consistent first-party registrations."""
from jarvix.domain import ToolResult, ToolSpec

TEXT = {"type": "string", "maxLength": 4096}
ID = {"type": "string", "minLength": 1, "maxLength": 160}
BOOL = {"type": "boolean"}


def string(maximum=4096, minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def integer(minimum=0, maximum=1000):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def array(items=None, maximum=100):
    return {"type": "array", "items": items or TEXT, "maxItems": maximum}


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def register(registry, name, description, properties, required, handler, level=1, permission="local.read", risk=None):
    def run(arguments):
        value = handler(**arguments)
        return value if isinstance(value, ToolResult) else ToolResult(True, value)
    registry.register(ToolSpec(name, description, schema(properties, required), permission,
                               risk or ("read" if level == 1 else "write"), level), run)
