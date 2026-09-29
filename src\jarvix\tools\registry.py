"""Typed, schema-validated capabilities. Permissions belong to the orchestrator."""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import check_cancelled


class ToolRegistry:
    """Register trusted handlers; validate all model-generated input before dispatch.

    This class deliberately contains no automatic permission grants. The caller must
    obtain execution approval before execute(), and disclosure approval before
    sending a local result to a remote provider.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Callable[[dict], ToolResult], Draft202012Validator]] = {}

    def register(self, spec: ToolSpec, handler: Callable[[dict], ToolResult]) -> None:
        if spec.permission_level not in (None, 1, 2, 3):
            raise ValueError("Invalid permission level.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", spec.name):
            raise ValueError("Invalid tool name.")
        if spec.name in self._tools:
            raise ValueError("Tool is already registered.")
        if not callable(handler):
            raise ValueError("A tool handler must be callable.")
        if spec.parameters.get("type") != "object":
            raise ValueError("Tool arguments must use an object schema.")
        try:
            Draft202012Validator.check_schema(spec.parameters)
        except SchemaError as exc:
            raise ValueError("Invalid tool argument schema.") from exc
        stored = copy.deepcopy(spec)
        self._tools[spec.name] = (stored, handler, Draft202012Validator(stored.parameters))

    def specs(self) -> list[ToolSpec]:
        return [copy.deepcopy(entry[0]) for entry in self._tools.values()]

    def get(self, name: str) -> ToolSpec:
        if not isinstance(name, str) or name not in self._tools:
            raise ValueError("Unknown tool.")
        return copy.deepcopy(self._tools[name][0])

    def validate(self, name: str, args: dict) -> ToolResult | None:
        """Check arguments without executing; None means permission can be requested."""
        if not isinstance(name, str) or name not in self._tools:
            return ToolResult(False, error="Unknown tool.")
        validator = self._tools[name][2]
        try:
            # Reject NaN, arbitrary Python objects and oversized arguments too.
            if not isinstance(args, dict) or len(json.dumps(args, allow_nan=False)) > 48_000:
                return ToolResult(False, error="Tool arguments must be a bounded JSON object.")
            if not validator.is_valid(args):
                return ToolResult(False, error="Arguments do not match the tool schema.")
            return None
        except Exception:
            return ToolResult(False, error="Tool arguments must be a bounded JSON object.")

    def execute(self, name: str, args: dict) -> ToolResult:
        failure = self.validate(name, args)
        if failure is not None:
            return failure
        handler = self._tools[name][1]
        try:
            check_cancelled()
            result = handler(copy.deepcopy(args))
            if not isinstance(result, ToolResult):
                return ToolResult(False, error="The tool returned an invalid result.")
            if len(json.dumps(result.as_dict(), ensure_ascii=False, allow_nan=False)) > 64_000:
                return ToolResult(False, error="Tool result is too large. Narrow the request.")
            if result.ok and self._tools[name][0].result_schema:
                if not Draft202012Validator(self._tools[name][0].result_schema).is_valid(result.data):
                    return ToolResult(False, error="The tool result did not match its schema.")
            return result
        except InterruptedError:
            return ToolResult(False, error="Operation stopped or timed out.")
        except (ValueError, FileNotFoundError, PermissionError):
            return ToolResult(False, error="The request is invalid, outside allowed access, or its target is unavailable.")
        except Exception:
            # Never expose exception text: it can contain credentials, local paths,
            # database contents, subprocess output or provider response bodies.
            return ToolResult(False, error="The tool could not complete this request.")
