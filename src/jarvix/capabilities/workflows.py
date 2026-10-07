"""Persisted workflows and routines composed exclusively from registered tools."""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import islice

import psutil

from jarvix.capabilities.schema import BOOL, ID, array, enum, register, string
from jarvix.capabilities.operator_graph import references as result_references
from jarvix.capabilities.workflow_conditions import (
    ConditionEvaluator, clock_time, network_connected, process_names, weekdays,
)
from jarvix.capabilities.workflow_values import NAME, bounded, resolve, validate_references
from jarvix.domain import ToolResult
from jarvix.runtime import CURRENT, check_cancelled, operation
from jarvix.storage import now_iso

TRIGGERS = ("manual", "schedule", "interval", "at_time", "jarvix_start", "windows_start", "app_start",
            "app_closed", "file_created", "file_modified", "folder_change", "clipboard_changed",
            "battery_below", "cpu_above", "memory_above", "network_connected", "network_disconnected",
            "task_due", "hotkey")
WORKFLOW_HOTKEYS = {f"{prefix}+F{key}": (modifiers, 0x6F + key)
                   for prefix, modifiers in (("Ctrl+Alt", 0x0003), ("Ctrl+Shift", 0x0006))
                   for key in range(1, 13)}
# Opt-in grants are scoped to immutable exact arguments, not blanket tool access.
# No UI input, arbitrary command execution, external send, or destructive action.
BACKGROUND_OPT_IN = frozenset({
    "apps.open", "files.open_folder", "files.copy", "files.move", "files.create_folder",
    "web.open", "tasks.create", "notes.create", "notifications.create", "backup.create",
})
RETRY_SAFE = frozenset({"tasks.list", "projects.list", "system.status", "system.processes",
                        "files.inspect", "files.list", "files.folder_summary", "apps.search"})
APPROVED_BACKGROUND_CALLS = contextvars.ContextVar("jarvix_workflow_approvals", default=frozenset())
DEFINITION_FIELDS = ("name", "kind", "trigger", "config", "conditions", "steps", "enabled", "approved_tools", "variables")


def _definition_values(row):
    # Empty variables are omitted so immutable approvals from 0.5 remain valid.
    return {key: row[key] for key in DEFINITION_FIELDS if key in row}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def background_allowed(name, arguments):
    return name in BACKGROUND_OPT_IN and fingerprint([name, arguments]) in APPROVED_BACKGROUND_CALLS.get()


def _number(value, low, high, label):
    if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return value


def _actions(steps):
    for step in steps:
        if step["kind"] == "action":
            yield step
        elif step["kind"] == "branch":
            yield from _actions(step["then"])
            yield from _actions(step["else"])
        elif step["kind"] in {"foreach", "subflow"}:
            yield from _actions(step["steps"])


class _RunCancellation(threading.Event):
    """A run can inherit shutdown without its Cancel button stopping the scheduler."""

    def __init__(self, *parents):
        super().__init__()
        self.parents = tuple(parent for parent in parents if parent is not None)

    def is_set(self):
        return super().is_set() or any(parent.is_set() for parent in self.parents)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while not self.is_set():
            remaining = .05 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                return self.is_set()
            super().wait(min(.05, remaining))
        return True


def _outcome_ok(result):
    if not result.ok:
        return False
    if isinstance(result.data, dict):
        if any(result.data.get(key) is False for key in ("ok", "completed", "matched")):
            return False
        if result.data.get("verified") is False and not result.data.get("requested"):
            return False
    return True


class WorkflowService:
    def __init__(self, services):
        self.s = services
        self.conditions = ConditionEvaluator(services)
        self._lock = threading.RLock()
        self._tick_lock = threading.Lock()
        self._active = {}
        self._started = set()
        self._clipboard_state = {}
        self._failed_triggers = set()
        # A prior interrupted process cannot still own these runs.
        for row in self.s.records.list("workflow_run"):
            if row.get("status") in {"running", "paused"}:
                row.update(status="interrupted", ok=False, ended_at=now_iso(), error="Jarvix stopped during this workflow.")
                self.s.records.put("workflow_run", row, row["id"])

    def list(self, kind=None):
        return [self._with_next_run(row) for row in self.s.records.list("workflow")
                if kind is None or row["kind"] == kind]

    def get(self, id):
        return self._with_next_run(self.s.records.get("workflow", id))

    def _config(self, trigger, config):
        if trigger not in TRIGGERS or not isinstance(config, dict):
            raise ValueError("Invalid workflow trigger.")
        allowed = {"schedule": {"time", "weekdays"}, "interval": {"minutes"}, "at_time": {"at"},
                   "app_start": {"name"}, "app_closed": {"name"}, "file_created": {"path"},
                   "file_modified": {"path"}, "folder_change": {"path"}, "clipboard_changed": {"opt_in"},
                   "battery_below": {"threshold"}, "cpu_above": {"threshold"}, "memory_above": {"threshold"},
                   "hotkey": {"shortcut"}}
        if set(config) - allowed.get(trigger, set()):
            raise ValueError("Unknown trigger configuration field.")
        value = copy.deepcopy(config)
        if trigger == "schedule":
            clock_time(value.get("time"))
            value["weekdays"] = weekdays(value.get("weekdays", list(range(7))))
        elif trigger == "interval":
            value["minutes"] = _number(value.get("minutes", 60), 1, 10080, "Interval")
        elif trigger == "at_time":
            try:
                when = datetime.fromisoformat(value["at"].replace("Z", "+00:00"))
            except (ValueError, KeyError, AttributeError) as exc:
                raise ValueError("Choose an ISO-8601 timestamp including a timezone.") from exc
            if when.tzinfo is None:
                raise ValueError("Scheduled time requires a timezone.")
            value["at"] = when.astimezone(timezone.utc).isoformat()
        elif trigger in {"app_start", "app_closed"}:
            self.conditions.validate({"kind": "app_running", "name": value.get("name")})
        elif trigger in {"file_created", "file_modified", "folder_change"}:
            if not isinstance(value.get("path"), str):
                raise ValueError("Choose an allowed trigger directory.")
            path = self.s.files.path(value["path"])
            if not path.is_dir():
                raise ValueError("Choose an allowed trigger directory.")
            value["path"] = str(path)
        elif trigger in {"battery_below", "cpu_above", "memory_above"}:
            value["threshold"] = _number(value.get("threshold", 20 if trigger == "battery_below" else 80), 0, 100, "Threshold")
        elif trigger == "clipboard_changed":
            if value.get("opt_in") is not True:
                raise ValueError("Clipboard trigger requires its own explicit opt-in.")
        elif trigger == "hotkey":
            shortcut = value.get("shortcut")
            canonical = next((key for key in WORKFLOW_HOTKEYS if isinstance(shortcut, str)
                              and key.casefold() == shortcut.casefold()), None)
            if canonical is None:
                raise ValueError("Choose Ctrl+Alt+F1–F12 or Ctrl+Shift+F1–F12.")
            value["shortcut"] = canonical
        return value

    def _steps(self, steps, depth=0, budget=None, results=None, variables=None, item=False, readonly=False, stack=()):
        budget = [0] if budget is None else budget
        results = set() if results is None else results
        variables = set() if variables is None else variables
        if not isinstance(steps, list) or depth > 3:
            raise ValueError("Workflow nesting is limited to three levels.")
        cleaned = []
        for raw in steps:
            budget[0] += 1
            if budget[0] > 32 or not isinstance(raw, dict):
                raise ValueError("A workflow supports at most 32 structured blocks.")
            kind = raw.get("kind")
            if kind == "action":
                if set(raw) - {"kind", "tool", "arguments", "retries", "on_error", "id"}:
                    raise ValueError("Unknown action field.")
                name, args = raw.get("tool"), raw.get("arguments")
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise ValueError("Each action requires a registered tool and structured arguments.")
                spec = self.s.registry.get(name)
                if readonly and (spec.risk != "read" or spec.permission_level != 1):
                    raise ValueError("Collection loops support only read-only Level 1 actions.")
                if name.startswith("operator."):
                    raise ValueError("Operator sessions cannot be nested inside workflows; use registered action tools.")
                if name.startswith(("workflows.", "routines.")) and name not in {"workflows.run", "routines.run"}:
                    raise ValueError("Workflow management cannot be nested in workflow actions.")
                references = validate_references(args, results, variables, item)
                if not references and self.s.registry.validate(name, args):
                    raise ValueError("Workflow action arguments do not match the tool schema.")
                retries = raw.get("retries", 0)
                if type(retries) is not int or not 0 <= retries <= 1:
                    raise ValueError("Actions allow at most one retry.")
                if retries and (spec.permission_level != 1 or name not in RETRY_SAFE):
                    raise ValueError("Retry is available only for explicitly safe read operations.")
                on_error = raw.get("on_error", "stop")
                if on_error not in {"stop", "continue"}:
                    raise ValueError("Choose stop or continue for failure handling.")
                value = {"kind": kind, "tool": name, "arguments": bounded(args),
                         "retries": retries, "on_error": on_error}
                if "id" in raw:
                    if not isinstance(raw["id"], str) or not NAME.fullmatch(raw["id"]) or raw["id"] in results:
                        raise ValueError("Action output IDs must be unique bounded names.")
                    results.add(raw["id"])
                    value["id"] = raw["id"]
                cleaned.append(value)
            elif kind == "delay":
                if set(raw) != {"kind", "seconds"}:
                    raise ValueError("A delay requires seconds.")
                cleaned.append({"kind": kind, "seconds": _number(raw["seconds"], 0, 60, "Delay")})
            elif kind == "branch":
                if set(raw) - {"kind", "condition", "then", "else"}:
                    raise ValueError("Unknown branch field.")
                condition = self.conditions.validate(raw.get("condition"))
                if condition["kind"] == "result":
                    validate_references({"$ref": condition["ref"]}, results, variables, item)
                then_ids, else_ids = set(results), set(results)
                cleaned.append({"kind": kind, "condition": bounded(condition),
                    "then": self._steps(raw.get("then", []), depth + 1, budget, then_ids, variables, item, readonly, stack),
                    "else": self._steps(raw.get("else", []), depth + 1, budget, else_ids, variables, item, readonly, stack)})
                results.update(then_ids & else_ids)
            elif kind == "set":
                if set(raw) != {"kind", "name", "value"} or not isinstance(raw["name"], str) or not NAME.fullmatch(raw["name"]):
                    raise ValueError("Variable blocks need a bounded name and JSON value.")
                validate_references(raw["value"], results, variables, item)
                variables.add(raw["name"])
                if len(variables) > 32:
                    raise ValueError("Workflow supports at most 32 named variables in a scope.")
                cleaned.append({"kind": kind, "name": raw["name"], "value": bounded(raw["value"])})
            elif kind == "foreach":
                if set(raw) - {"kind", "items", "steps", "limit"}:
                    raise ValueError("Unknown collection loop field.")
                collection = raw.get("items")
                validate_references(collection, results, variables, item)
                limit = raw.get("limit", 20)
                if type(limit) is not int or not 1 <= limit <= 20 or not isinstance(collection, (dict, list)):
                    raise ValueError("Loop over a JSON collection with a limit from 1 to 20.")
                if isinstance(collection, dict) and set(collection) != {"$ref"}:
                    raise ValueError("Loop collection must be a list or structured reference.")
                if isinstance(collection, list) and len(collection) > limit:
                    raise ValueError("Collection exceeds its explicit loop limit.")
                cleaned.append({"kind": kind, "items": bounded(collection), "limit": limit,
                    "steps": self._steps(raw.get("steps", []), depth + 1, budget, set(results), set(variables), True, True, stack)})
            elif kind == "subflow":
                if set(raw) - {"kind", "workflow_id", "approval_fingerprint", "steps", "variables", "conditions"}:
                    raise ValueError("Unknown subflow field.")
                target = raw.get("workflow_id")
                if not isinstance(target, str) or target in stack:
                    raise ValueError("Subflow recursion is not supported.")
                row = self.s.records.get("workflow", target)
                if fingerprint(_definition_values(row)) != row.get("approval_fingerprint"):
                    raise PermissionError("Subflow changed since its approval.")
                if "approval_fingerprint" in raw and raw["approval_fingerprint"] != row["approval_fingerprint"]:
                    raise PermissionError("Subflow was edited; review and save the parent again.")
                child_variables = bounded(row.get("variables", {}))
                child = self._steps(row["steps"], depth + 1, budget, variables=set(child_variables),
                                    item=item, readonly=readonly, stack=(*stack, target))
                value = {"kind": kind, "workflow_id": target, "approval_fingerprint": row["approval_fingerprint"],
                         "steps": child, "variables": child_variables, "conditions": copy.deepcopy(row["conditions"])}
                if any(raw[key] != value[key] for key in ("steps", "variables", "conditions") if key in raw):
                    raise PermissionError("Subflow snapshot changed; review and save the parent again.")
                cleaned.append(value)
            else:
                raise ValueError("Choose an action, delay, branch, variable, collection loop or subflow block.")
        return cleaned

    def _definition(self, name, steps, trigger="manual", config=None, conditions=None, enabled=False,
                    kind="workflow", approved_tools=None, id=None, variables=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("Workflow name must contain 1 to 160 characters.")
        if type(enabled) is not bool or kind not in {"workflow", "routine"}:
            raise ValueError("Invalid workflow kind or enabled state.")
        if kind == "routine" and trigger != "manual":
            raise ValueError("Routines run manually; use a workflow for triggers.")
        if conditions is not None and (not isinstance(conditions, list) or len(conditions) > 12):
            raise ValueError("At most 12 conditions are supported.")
        approved_tools = [] if approved_tools is None else approved_tools
        if not isinstance(approved_tools, list) or len(approved_tools) > 32 or any(
            not isinstance(name, str) or name not in BACKGROUND_OPT_IN for name in approved_tools
        ):
            raise ValueError("Only supported reversible background tools can be approved.")
        variables = {} if variables is None else bounded(variables)
        if not isinstance(variables, dict) or len(variables) > 32 or any(
            not isinstance(key, str) or not NAME.fullmatch(key) for key in variables
        ):
            raise ValueError("Use at most 32 named JSON workflow variables.")
        for condition in conditions or []:
            if isinstance(condition, dict) and condition.get("kind") == "result":
                validate_references({"$ref": condition.get("ref")}, variables=variables)
        value = {"name": name.strip(), "kind": kind, "trigger": trigger,
                 "config": self._config(trigger, {} if config is None else config),
                 "conditions": [bounded(self.conditions.validate(c)) for c in (conditions or [])],
                 "steps": self._steps(steps, variables=set(variables), stack=(id,) if id else ()),
                 "enabled": enabled, "approved_tools": sorted(set(approved_tools))}
        if variables:
            value["variables"] = variables
        if not value["steps"]:
            raise ValueError("Add at least one workflow block.")
        if trigger != "manual":
            self._background_calls(value)
        if id is not None:
            self.s.records.get("workflow", id)
        return value

    def _background_calls(self, row):
        calls = set()
        for action in _actions(row["steps"]):
            name = action["tool"]
            spec = self.s.registry.get(name)
            if name in self.s.UNATTENDED_TOOL_ALLOWLIST and spec.permission_level in {1, 2}:
                continue
            if name not in row["approved_tools"] or name not in BACKGROUND_OPT_IN or spec.permission_level != 2:
                raise PermissionError("Scheduled actions require exact approval and a supported reversible tool.")
            if result_references(action["arguments"]):
                raise PermissionError("Background mutations require literal exact approved arguments, not dynamic values.")
            if name == "apps.open" and self.s.apps.arguments_for(action["arguments"]["id"]):
                raise PermissionError("Applications with configured command arguments cannot run unattended.")
            calls.add(fingerprint([name, action["arguments"]]))
        return frozenset(calls)

    def preview_definition(self, **definition):
        row = self._definition(**definition)
        return {**row, "actions": [{"tool": action["tool"], "arguments": action["arguments"],
                    "permission_level": self.s.registry.get(action["tool"]).permission_level}
                    for action in _actions(row["steps"])],
                "background_opt_in": bool(row["approved_tools"]), "next_run": self._next_run(row)}

    def save(self, name, steps, trigger="manual", config=None, conditions=None, enabled=False,
             kind="workflow", approved_tools=None, id=None, variables=None):
        with self._lock:
            row = self._definition(name, steps, trigger, config, conditions, enabled, kind, approved_tools, id, variables)
            row["approval_fingerprint"] = fingerprint(row)
            row.update(last_run_at=None, last_state=None, last_trigger_at=None)
            record_id = self.s.records.put("workflow", row, id)
            self._clipboard_state.pop(record_id, None)
            self._failed_triggers.discard(record_id)
        return {"id": record_id, "enabled": enabled}

    def preview(self, id):
        row = self.get(id)
        return {"id": id, **self.preview_definition(**_definition_values(row))}

    def _verified(self, id):
        row = self.s.records.get("workflow", id)
        definition = self._definition(**_definition_values(row))
        if fingerprint(definition) != row.get("approval_fingerprint"):
            raise PermissionError("Workflow changed since its approval; review and save it again.")
        return row

    def toggle(self, id, enabled):
        if type(enabled) is not bool:
            raise ValueError("Enabled must be a boolean.")
        with self._lock:
            row = self._verified(id)
            row["enabled"] = enabled
            row["approval_fingerprint"] = fingerprint(_definition_values(row))
            self.s.records.put("workflow", row, id)
        return {"id": id, "enabled": enabled}

    def duplicate(self, id, name):
        row = self.get(id)
        values = _definition_values(row)
        values.update(name=name, enabled=False)
        return self.save(**values)

    def delete(self, id):
        with self._lock:
            if any(value["workflow_id"] == id for value in self._active.values()):
                raise ValueError("Cancel the active run before deleting this workflow.")
            self.s.records.get("workflow", id)
            self.s.records.delete("workflow", id)
            self._clipboard_state.pop(id, None)
            self._failed_triggers.discard(id)
            self._started.discard(id)
        return {"deleted": True}

    def export(self, id):
        row = self.get(id)
        return {"version": 1, "workflow": _definition_values(row)}

    def import_workflow(self, definition):
        if not isinstance(definition, dict):
            raise ValueError("Import a structured workflow object.")
        if "workflow" in definition:
            if definition.get("version") != 1 or set(definition) != {"version", "workflow"}:
                raise ValueError("Unsupported workflow export version.")
            definition = definition["workflow"]
        if not isinstance(definition, dict) or set(definition) - set(DEFINITION_FIELDS):
            raise ValueError("Unknown imported workflow fields.")
        values = copy.deepcopy(definition)
        # Imported schedules remain disabled. Never import an approval grant.
        values.update(enabled=False, approved_tools=[])
        # Reversible actions can be imported into a manual draft for review.
        values.update(trigger="manual", config={})
        return self.save(**values)

    def test(self, id):
        row = self._verified(id)
        return self._test(row)

    def test_definition(self, **definition):
        return self._test(self._definition(**definition))

    def debug(self, id, cancel=None, on_event=None):
        return self.run(id, cancel=cancel, on_event=on_event, test_mode=True)

    def templates(self):
        return [{"name": "Review today's tasks", "trigger": "manual", "steps": [
            {"kind": "action", "id": "tasks", "tool": "tasks.list", "arguments": {}}]},
            {"name": "Startup health check", "trigger": "jarvix_start", "enabled": False, "steps": [
            {"kind": "action", "id": "health", "tool": "system.status", "arguments": {}},
            {"kind": "action", "tool": "tasks.list", "arguments": {}}]}]

    def _test(self, row):
        values = {"variables": row.get("variables", {}), "results": {}}
        results = [{"kind": c["kind"], "matches": self.conditions.evaluate(c, values=values)} for c in row["conditions"]]
        return {"valid": True, "conditions": results, "would_run": all(c["matches"] for c in results),
                "action_count": len(list(_actions(row["steps"]))), "actions_executed": 0,
                "next_run": self._next_run(row)}

    def history(self, id=""):
        return [row for row in self.s.records.list("workflow_run") if not id or row["workflow_id"] == id][:100]

    def _control(self, run_id):
        with self._lock:
            if run_id not in self._active:
                raise ValueError("This workflow run is no longer active.")
            return self._active[run_id]

    def pause(self, run_id):
        self._control(run_id)["pause"].set()
        return {"run_id": run_id, "paused": True}

    def resume(self, run_id):
        self._control(run_id)["pause"].clear()
        return {"run_id": run_id, "paused": False}

    def cancel(self, run_id):
        self._control(run_id)["cancel"].set()
        return {"run_id": run_id, "cancel_requested": True}

    def close(self):
        with self._lock:
            for control in self._active.values():
                control["cancel"].set()

    def run(self, id, unattended=False, cancel=None, on_event=None, test_mode=False):
        if type(test_mode) is not bool:
            raise ValueError("Test mode must be a boolean.")
        with self._lock:
            row = self._verified(id)
            if not self.s.settings.get("automations.enabled", True):
                raise PermissionError("Automations are disabled.")
            inherited = CURRENT.get()
            unattended = unattended or bool(inherited and inherited.unattended)
            if unattended and not row["enabled"]:
                raise PermissionError("Enable this workflow explicitly before background execution.")
            calls = self._background_calls(row) if unattended else frozenset()
            if any(value["workflow_id"] == id for value in self._active.values()):
                raise ValueError("This workflow is already running; recursive runs are blocked.")
            run = {"workflow_id": id, "name": row["name"], "status": "running", "ok": False,
                   "started_at": now_iso(), "steps": [], "unattended": unattended, "test_mode": test_mode,
                   "actions_executed": 0, "actions_previewed": 0}
            run_id = self.s.records.put("workflow_run", run)
            if on_event is None and inherited:
                on_event = inherited.on_event
            control = {"workflow_id": id, "cancel": _RunCancellation(
                cancel, inherited.cancel if inherited else None), "pause": threading.Event()}
            self._active[run_id] = control
        token = APPROVED_BACKGROUND_CALLS.set(calls)
        deadline = inherited.deadline if inherited else float("inf")
        values = {"variables": copy.deepcopy(row.get("variables", {})), "results": {}}

        def emit():
            self.s.records.put("workflow_run", run, run_id)
            if on_event:
                try:
                    on_event("workflow", {"run_id": run_id, **copy.deepcopy(run)})
                except Exception:
                    pass  # A closed UI subscriber must not interrupt safe work.

        def checkpoint():
            if prior_checkpoint:
                prior_checkpoint()
            if control["cancel"].is_set():
                raise InterruptedError("Workflow cancelled.")
            while control["pause"].is_set():
                if run["status"] != "paused":
                    run["status"] = "paused"
                    emit()
                current = CURRENT.get()
                # An inherited pause must also yield to the active child run's Cancel.
                if control["cancel"].wait(.05) or (current and current.cancel.is_set()):
                    raise InterruptedError("Workflow cancelled.")
                if current and time.monotonic() > current.deadline:
                    raise InterruptedError("Workflow timed out.")
            if run["status"] == "paused":
                run["status"] = "running"
                emit()

        try:
            with operation(cancel=control["cancel"], timeout=self.s.settings.get("agent.timeout_seconds", 120),
                           max_steps=64, unattended=unattended, on_event=on_event) as context:
                prior_checkpoint = getattr(context, "checkpoint", None)
                prior_cancel, deadline = context.cancel, context.deadline
                # Runtime-level checks propagate pause/cancel into nested capabilities.
                context.cancel = control["cancel"]
                context.checkpoint = checkpoint
                try:
                    emit()
                    checkpoint()
                    check_cancelled()
                    if not all(self.conditions.evaluate(c, values=values) for c in row["conditions"]):
                        run.update(status="skipped", ok=True)
                    else:
                        self._execute_steps(row["steps"], run, checkpoint, emit, unattended, values=values)
                        failed = any(not step.get("ok", True) for step in run["steps"])
                        run.update(status="partial" if failed else "tested" if test_mode else "completed", ok=not failed)
                finally:
                    context.cancel = prior_cancel
                    context.checkpoint = prior_checkpoint
        except InterruptedError:
            timed_out = not control["cancel"].is_set() and time.monotonic() > deadline
            run.update(status="timed_out" if timed_out else "cancelled", ok=False,
                       error="Workflow stopped or exceeded its time limit.")
        except Exception:
            run.update(status="failed", ok=False, error="Workflow stopped; review the failed action and permissions.")
        finally:
            APPROVED_BACKGROUND_CALLS.reset(token)
            run["ended_at"] = now_iso()
            for step in run["steps"]:
                if step["status"] == "running":
                    step.update(ok=False, status=run["status"],
                                error="Interrupted action may have completed; inspect state before retrying.")
                    step["failure_code"] = ("deadline_exceeded" if run["status"] == "timed_out" else
                                            "cancelled" if run["status"] == "cancelled" else "workflow_stopped")
                step.setdefault("ended_at", run["ended_at"])
            try:
                emit()
                with self._lock:
                    # Keep a concurrently edited definition and its permissions intact.
                    current_row = self.s.records.get("workflow", id)
                    current_row["last_run_at"] = run["ended_at"]
                    self.s.records.put("workflow", current_row, id)
            finally:
                with self._lock:
                    self._active.pop(run_id, None)
            self.s.repository.audit("automation", f"Workflow {run['status']}; {len(run['steps'])} evaluated block(s)")
        return {"run_id": run_id, **run}

    def _execute_steps(self, steps, run, checkpoint, emit, unattended, prefix="", values=None, read_only=False):
        values = {"variables": {}, "results": {}} if values is None else values
        for index, step in enumerate(steps):
            checkpoint()
            check_cancelled()
            path = f"{prefix}{index + 1}"
            if len(run["steps"]) >= 128:
                raise ValueError("Workflow evaluated more than 128 blocks; reduce its collection limits.")
            entry = {"path": path, "kind": step["kind"], "status": "running", "started_at": now_iso()}
            if step["kind"] == "action":
                entry["tool"] = step["tool"]
            run["steps"].append(entry)
            emit()
            started = time.monotonic()
            if step["kind"] == "delay":
                if run["test_mode"]:
                    entry.update(ok=True, status="preview_only", ended_at=now_iso())
                    emit()
                    continue
                deadline = time.monotonic() + step["seconds"]
                while time.monotonic() < deadline:
                    checkpoint()
                    check_cancelled()
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
                entry.update(ok=True, status="completed")
            elif step["kind"] == "branch":
                matches = self.conditions.evaluate(step["condition"], values=values)
                entry.update(ok=True, status="completed", matched=matches)
                self._execute_steps(step["then"] if matches else step["else"], run, checkpoint, emit, unattended,
                                    prefix=path + ".", values=values, read_only=read_only)
            elif step["kind"] == "set":
                values["variables"][step["name"]] = resolve(step["value"], values)
                bounded(values["variables"])
                entry.update(ok=True, status="completed")
            elif step["kind"] == "foreach":
                items = resolve(step["items"], values)
                if not isinstance(items, list) or len(items) > step["limit"]:
                    entry.update(ok=False, status="failed", failure_code="collection_limit")
                    raise ValueError("Collection no longer fits the approved read-only loop limit.")
                entry["item_count"] = len(items)
                for number, item in enumerate(items, 1):
                    child = {"variables": copy.deepcopy(values["variables"]),
                             "results": copy.deepcopy(values["results"]), "item": item}
                    self._execute_steps(step["steps"], run, checkpoint, emit, unattended,
                                        prefix=path + f".{number}.", values=child, read_only=True)
                entry.update(ok=True, status="completed")
            elif step["kind"] == "subflow":
                current = self.s.records.get("workflow", step["workflow_id"])
                if current["approval_fingerprint"] != step["approval_fingerprint"] or fingerprint(
                    _definition_values(current)
                ) != step["approval_fingerprint"]:
                    raise PermissionError("Subflow changed during execution.")
                child = {"variables": copy.deepcopy(step["variables"]), "results": {}}
                if "item" in values:
                    child["item"] = values["item"]
                matches = all(self.conditions.evaluate(c, values=child) for c in step["conditions"])
                if matches:
                    self._execute_steps(step["steps"], run, checkpoint, emit, unattended,
                                        prefix=path + ".", values=child, read_only=read_only)
                entry.update(ok=True, status="completed" if matches else "skipped")
            else:
                spec = self.s.registry.get(step["tool"])
                if read_only and (spec.risk != "read" or spec.permission_level != 1):
                    raise PermissionError("A loop action is no longer read-only.")
                if run["test_mode"] and (spec.risk != "read" or spec.permission_level != 1):
                    run["actions_previewed"] += 1
                    entry.update(ok=True, status="preview_only", ended_at=now_iso())
                    emit()
                    continue
                result = None
                try:
                    arguments = bounded(resolve(step["arguments"], values))
                except (KeyError, IndexError, ValueError):
                    entry.update(ok=False, status="failed", failure_code="reference_unavailable")
                    raise ValueError("An earlier workflow output is unavailable.") from None
                for attempt in range(step["retries"] + 1):
                    checkpoint()
                    check_cancelled()
                    result = self.s.execute_tool(step["tool"], arguments,
                                                 approve=(lambda _: False) if unattended else None)
                    result, invalid = self.s.operator._validate_result(step["tool"], result)
                    run["actions_executed"] += 1
                    entry["attempts"] = attempt + 1
                    if _outcome_ok(result):
                        break
                    # No repeated access requests after denial or cancellation.
                    if any(word in (result.error or "").casefold() for word in ("permission", "denied", "cancel", "timed out")):
                        break
                check_cancelled()
                succeeded = _outcome_ok(result)
                entry.update(ok=succeeded, status="completed" if succeeded else "failed")
                if "id" in step:
                    values["results"][step["id"]] = bounded(result.as_dict())
                    bounded(values["results"])
                    entry["output_id"] = step["id"]
                if isinstance(result.data, dict):
                    if "verified" in result.data:
                        entry["verified"] = result.data["verified"] is True
                    if succeeded and result.data.get("requested") and not entry.get("verified"):
                        entry.update(status="requested", verified=False)
                if not succeeded:
                    entry["failure_code"] = "invalid_result" if invalid else "permission_denied" if result.sensitivity == "public" else "tool_failed"
                    entry["error"] = "Tool failed or permission was denied; no private tool values were stored."
                    emit()
                    if step["on_error"] == "stop":
                        raise RuntimeError("Workflow action failed.")
            entry.update(ended_at=now_iso(), elapsed_seconds=round(time.monotonic() - started, 3))
            emit()

    def _next_run(self, row, now=None):
        if not row.get("enabled") or row["trigger"] not in {"schedule", "interval", "at_time"}:
            return None
        now = now or datetime.now().astimezone()
        config = row["config"]
        if row["trigger"] == "interval":
            last = row.get("last_trigger_at") or row.get("last_run_at")
            when = datetime.fromisoformat(last) + timedelta(minutes=config["minutes"]) if last else now
            return max(when, now).isoformat()
        if row["trigger"] == "at_time":
            return None if row.get("last_trigger_at") else config["at"]
        local = now.astimezone()
        last = row.get("last_trigger_at")
        last = datetime.fromisoformat(last) if last else None
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            # Construct a fresh local datetime so DST follows that date's OS rules.
            candidate = datetime.combine(day, clock_time(config["time"])).astimezone()
            if candidate.weekday() in config["weekdays"] and candidate >= now and (not last or candidate > last):
                return candidate.isoformat()
        return None

    def _with_next_run(self, row):
        return {**row, "next_run": self._next_run(row)}

    def _event_state(self, row):
        trigger, config = row["trigger"], row["config"]
        if trigger in {"app_start", "app_closed"}:
            return config["name"].casefold() in process_names()
        if trigger in {"network_connected", "network_disconnected"}:
            return network_connected()
        if trigger in {"file_created", "file_modified", "folder_change"}:
            folder = self.s.files.path(config["path"])
            values = {}
            for index, entry in enumerate(islice(folder.iterdir(), 1001)):
                check_cancelled()
                if index == 1000:
                    raise ValueError("Watch a directory containing at most 1000 direct entries.")
                try:
                    path = self.s.files.path(str(entry))
                    stat = path.stat()
                    # Filenames are private; persist only hashes and metadata.
                    values[fingerprint(path.name)] = [stat.st_size, stat.st_mtime_ns]
                except (OSError, ValueError, PermissionError):
                    continue
            return values
        if trigger == "clipboard_changed":
            if not config.get("opt_in") or not self.s.settings.get("clipboard.enabled", False):
                return None
            # This hash is held only in RAM; clipboard contents never reach records/logs.
            return fingerprint(self.s.clipboard.read()["text"])
        if trigger == "battery_below":
            battery = psutil.sensors_battery()
            return bool(battery and not battery.power_plugged and battery.percent < config["threshold"])
        if trigger == "cpu_above":
            return psutil.cpu_percent() > config["threshold"]
        if trigger == "memory_above":
            return psutil.virtual_memory().percent > config["threshold"]
        if trigger == "task_due":
            current = datetime.now(timezone.utc)
            return sorted(item["id"] for item in self.s.list_tasks() if item["status"] == "open"
                          and item.get("due_at") and datetime.fromisoformat(item["due_at"]) <= current)
        return None

    def _should_fire(self, row, now):
        trigger = row["trigger"]
        last = row.get("last_trigger_at")
        last = datetime.fromisoformat(last) if last else None
        if trigger == "jarvix_start":
            fire = row["id"] not in self._started
            self._started.add(row["id"])
            return fire
        if trigger == "windows_start":
            boot = str(int(psutil.boot_time()))
            fire = row.get("last_state") != boot
            row["last_state"] = boot
            return fire
        if trigger == "interval":
            return not last or (now - last).total_seconds() >= row["config"]["minutes"] * 60
        if trigger == "at_time":
            return not last and now >= datetime.fromisoformat(row["config"]["at"])
        if trigger == "schedule":
            local = now.astimezone()
            scheduled = datetime.combine(local.date(), clock_time(row["config"]["time"])).astimezone()
            return local.weekday() in row["config"]["weekdays"] and now >= scheduled and (not last or last < scheduled)
        state = self._event_state(row)
        previous = row.get("last_state")
        if trigger == "clipboard_changed":
            previous = self._clipboard_state.get(row["id"])
            self._clipboard_state[row["id"]] = state
            return state is not None and previous is not None and state != previous
        row["last_state"] = state
        if trigger in {"file_created", "task_due"}:
            return (trigger == "task_due" or previous is not None) and bool(set(state or []) - set(previous or []))
        if trigger == "file_modified":
            return previous is not None and any(key in previous and previous[key] != value for key, value in state.items())
        if trigger == "folder_change":
            return previous is not None and state != previous
        if trigger in {"app_closed", "network_disconnected"}:
            return previous is True and state is False
        return bool(state) and state != previous

    def tick(self, cancel=None, on_event=None):
        if not self.s.settings.get("automations.enabled", True) or not self._tick_lock.acquire(blocking=False):
            return []
        results = []
        try:
            for row in self.s.records.list("workflow"):
                check_cancelled()
                if cancel is not None and cancel.is_set():
                    break
                if not row["enabled"] or row["trigger"] in {"manual", "hotkey"}:
                    continue
                try:
                    with self._lock:
                        row = self._verified(row["id"])
                        if not row["enabled"] or row["trigger"] in {"manual", "hotkey"}:
                            continue
                        previous = copy.deepcopy(row)
                        now = datetime.now(timezone.utc)
                        fire = self._should_fire(row, now)
                        if fire:
                            row["last_trigger_at"] = now.isoformat()
                        if row != previous:
                            self.s.records.put("workflow", row, row["id"])
                    self._failed_triggers.discard(row["id"])
                    if fire:
                        results.append(self.run(row["id"], unattended=True, cancel=cancel, on_event=on_event))
                except InterruptedError:
                    raise
                except Exception:
                    if row["id"] not in self._failed_triggers:
                        self._failed_triggers.add(row["id"])
                        self.s.records.put("workflow_run", {"workflow_id": row["id"], "name": row["name"],
                            "status": "failed", "ok": False, "steps": [], "ended_at": now_iso(),
                            "error": "Trigger validation failed; review permissions and allowed paths."})
                        self.s.repository.audit("error", "Workflow trigger could not be evaluated")
            return results
        finally:
            self._tick_lock.release()

    def trigger_hotkey(self, shortcut, cancel=None, on_event=None):
        return [self.run(row["id"], unattended=True, cancel=cancel, on_event=on_event)
                for row in self.list() if row["enabled"] and row["trigger"] == "hotkey"
                and row["config"]["shortcut"].casefold() == shortcut.casefold()]


def setup(s, registry):
    service = s.workflows = WorkflowService(s)
    props = {"name": string(160), "steps": array({"type": "object"}, 32), "id": ID,
             "trigger": enum(*TRIGGERS), "config": {"type": "object"},
             "conditions": array({"type": "object"}, 12), "enabled": BOOL,
             "kind": enum("workflow", "routine"), "approved_tools": array(enum(*sorted(BACKGROUND_OPT_IN)), 32),
             "variables": {"type": "object", "maxProperties": 32}}

    def run(id):
        value = service.run(id)
        return ToolResult(value["ok"], value, None if value["ok"] else "Workflow needs attention.")

    def save_routine(name, steps, id=None, conditions=None, variables=None):
        return service.save(name, steps, id=id, conditions=conditions, kind="routine", variables=variables)

    register(registry, "workflows.list", "List workflows and reusable manual routines with next scheduled runs.",
             {"kind": enum("workflow", "routine")}, (), service.list)
    register(registry, "workflows.preview_definition", "Validate and preview a proposed workflow before asking to save it.",
             props, ("name", "steps"), service.preview_definition)
    register(registry, "workflows.test_definition", "Test proposed trigger configuration and conditions without saving or executing any actions.",
             props, ("name", "steps"), service.test_definition)
    register(registry, "workflows.save", "Show the exact trigger, conditions, steps and background grants; save only after fresh confirmation. Scheduled workflows require explicit enabled=true.",
             props, ("name", "steps"), service.save, 3)
    for name, description, handler in (
        ("preview", "Inspect a workflow and every required action permission.", service.preview),
        ("test", "Validate a workflow and evaluate conditions without running any actions.", service.test),
        ("export", "Export a local workflow as a portable structured definition.", service.export),
    ):
        register(registry, "workflows." + name, description, {"id": ID}, ("id",), handler)
    register(registry, "workflows.run", "Run a bounded workflow with permission checks and a persisted timeline.",
             {"id": ID}, ("id",), run, 2)
    register(registry, "workflows.toggle", "Review and confirm enabling or disabling an automation and its approved actions.",
             {"id": ID, "enabled": BOOL}, ("id", "enabled"), service.toggle, 3)
    register(registry, "workflows.duplicate", "Duplicate a workflow as a disabled draft.",
             {"id": ID, "name": string(160)}, ("id", "name"), service.duplicate, 2)
    register(registry, "workflows.delete", "Delete a saved workflow after confirmation.", {"id": ID}, ("id",), service.delete, 3)
    register(registry, "workflows.import", "Review an imported definition; imported workflows become manual disabled drafts with no background grants.",
             {"definition": {"type": "object"}}, ("definition",), service.import_workflow, 3)
    register(registry, "workflows.history", "Inspect persisted workflow timelines without private action values.",
             {"id": string(160, 0)}, (), service.history)
    register(registry, "workflows.debug", "Debug an approved workflow by executing only Level 1 reads; preview mutations and skip delays. Private outputs stay in memory.",
             {"id": ID}, ("id",), service.debug)
    register(registry, "workflows.templates", "List usable workflow templates as disabled definitions for explicit review and saving.",
             {}, (), service.templates)
    for name in ("pause", "resume", "cancel"):
        register(registry, "workflows." + name, f"{name.title()} an active workflow at a cooperative checkpoint.",
                 {"run_id": ID}, ("run_id",), getattr(service, name), 2)
    register(registry, "routines.list", "List reusable manual routines.", {}, (), lambda: service.list("routine"))
    register(registry, "routines.save", "Preview and confirm a reusable manual sequence of registered tool actions.",
             {key: props[key] for key in ("name", "steps", "id", "conditions", "variables")}, ("name", "steps"), save_routine, 3)
    register(registry, "routines.run", "Run a saved manual routine with normal per-action permissions.",
             {"id": ID}, ("id",), run, 2)
