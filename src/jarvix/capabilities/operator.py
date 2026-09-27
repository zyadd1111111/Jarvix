"""Bounded, previewed plans with persistent public progress and ephemeral private values."""
from __future__ import annotations

import copy
import json
import re
import threading
import time

from jsonschema import Draft202012Validator

from jarvix.capabilities.schema import ID, array, integer, register, schema, string
from jarvix.domain import PermissionRequest, ToolResult
from jarvix.runtime import CURRENT, check_cancelled, operation

EXPECTED = schema({"tool": string(80), "arguments": {"type": "object"},
                   "path": array({"type": ["string", "integer"]}, 12), "equals": {}}, ("tool", "arguments"))
STEP = schema({"id": string(60), "tool": string(80), "arguments": {"type": "object"},
               "expected": EXPECTED, "retries": integer(0, 1)}, ("id", "tool", "arguments"))
PLAN = schema({"goal": string(500), "steps": {**array(STEP, 24), "minItems": 1},
               "timeout_seconds": integer(1, 600)}, ("goal", "steps"))
PROHIBITED = ("operator.", "actions.undo")
TERMINAL = {"complete", "failed", "cancelled", "timed_out", "denied", "interrupted"}


def _path(value, path):
    for key in path:
        if not isinstance(value, (dict, list)) or isinstance(key, bool):
            raise ValueError("Reference does not identify a result field.")
        value = value[key]
    return copy.deepcopy(value)


def _references(value, earlier):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("A result reference must contain only $ref.")
            parts = value["$ref"].split(".")
            if len(parts) < 2 or parts[0] not in earlier or len(parts) > 12:
                raise ValueError("Only earlier step results can be referenced.")
            return True
        return any([_references(item, earlier) for item in value.values()])
    if isinstance(value, list):
        return any([_references(item, earlier) for item in value])
    return False


def _resolve(value, results):
    if isinstance(value, dict):
        if "$ref" in value:
            parts = value["$ref"].split(".")
            return _path(results[parts[0]], [int(p) if p.isdigit() else p for p in parts[1:]])
        return {key: _resolve(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, results) for item in value]
    return value


class OperatorService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._active = {}
        self._plans = {}
        self._results = {}
        self._mutations = {}
        self._listeners = []
        for row in self.s.records.list("operator_session"):
            if row.get("status") not in TERMINAL:
                row["status"] = "interrupted"
                for step in row["steps"]:
                    if step["status"] == "running":
                        step["status"] = "interrupted"
                self.s.records.put("operator_session", row, row["id"])

    def set_callback(self, callback):
        self._listeners = [callback] if callback else []

    def _publish(self, session, event=None):
        with self._lock:
            self.s.records.put("operator_session", session, session["id"])
            value = self.get(session["id"])
        for callback in [*self._listeners, *([event] if event else [])]:
            try:
                callback("operator_session", copy.deepcopy(value))
            except Exception:
                pass  # Progress delivery must never repeat an action.

    def list(self):
        return self.s.records.list("operator_session")[:100]

    def get(self, id):
        return self.s.records.get("operator_session", id)

    def _validate(self, plan, seed=None):
        if not Draft202012Validator(PLAN).is_valid(plan) or len(json.dumps(plan, allow_nan=False)) > 44000:
            raise ValueError("Use a bounded goal and 1–24 schema-valid steps.")
        current = CURRENT.get()
        needed = sum(1 + bool(step.get("expected")) for step in plan["steps"])
        if current and needed > current.steps_remaining:
            raise ValueError("This plan exceeds the remaining tool-step budget. Split it into smaller plans.")
        previous = set(seed or {})
        names = set()
        writes = set()
        enabled = set(self.s.enabled_tools())
        for step in plan["steps"]:
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,59}", step["id"]) or step["id"] in names:
                raise ValueError("Step IDs must be unique and contain no dots.")
            spec = self.s.registry.get(step["tool"])
            if spec.name.startswith(PROHIBITED) or spec.name not in enabled:
                raise ValueError("Recursive or disabled operator tools cannot be planned.")
            has_ref = _references(step["arguments"], previous)
            if not has_ref and self.s.registry.validate(spec.name, step["arguments"]):
                raise ValueError("Step arguments do not match the tool schema.")
            fingerprint = spec.name + json.dumps(step["arguments"], sort_keys=True)
            if spec.risk != "read":
                if fingerprint in writes:
                    raise ValueError("Repeated identical mutations are not a valid plan.")
                writes.add(fingerprint)
                if step.get("retries", 0):
                    raise ValueError("Automatic retries are available only for reads.")
            previous.add(step["id"])
            names.add(step["id"])
            expected = step.get("expected")
            if expected:
                verifier = self.s.registry.get(expected["tool"])
                if verifier.risk != "read" or verifier.permission_level == 3 or verifier.name.startswith(PROHIBITED):
                    raise ValueError("Verification must use a read-only tool.")
                if verifier.name not in enabled:
                    raise ValueError("Verification tool is disabled.")
                if not _references(expected["arguments"], previous) and self.s.registry.validate(verifier.name, expected["arguments"]):
                    raise ValueError("Invalid verification arguments.")
        return copy.deepcopy(plan)

    def preview(self, plan):
        plan = self._validate(plan)
        return {"goal": plan["goal"], "timeout_seconds": plan.get("timeout_seconds", 120),
                "steps": [{**step, "permission_level": self.s.registry.get(step["tool"]).permission_level,
                            "permission": self.s.registry.get(step["tool"]).permission} for step in plan["steps"]]}

    def _control(self, id, action):
        with self._lock:
            active = self._active.get(id)
            if not active:
                return {"accepted": False, "reason": "This session is not running."}
            if action == "cancel":
                active["cancel"].set()
                active["resume"].set()
            elif action == "pause":
                active["resume"].clear()
            else:
                active["resume"].set()
            return {"accepted": True, "action": action, "id": id}

    def pause(self, id):
        return self._control(id, "pause")

    def resume(self, id):
        return self._control(id, "resume")

    def cancel(self, id):
        return self._control(id, "cancel")

    def run(self, plan, approve=None, cancel=None, on_event=None, _seed=None, _parent=None, _mutations=None):
        plan = self._validate(plan, _seed)
        context = CURRENT.get()
        approval = approve or (context.approve if context else lambda _: False)
        event = on_event or (context.on_event if context else None)
        cancel = cancel or (context.cancel if context else threading.Event())
        resumed = threading.Event()
        resumed.set()
        session_id = self.s.repository.new_id()
        # No arguments, results, clipboard text, screenshots or typed values are kept in progress history.
        session = {"id": session_id, "goal": plan["goal"], "status": "awaiting_confirmation",
                   "parent_id": _parent, "timeout_seconds": plan.get("timeout_seconds", 120),
                   "steps": [{"id": step["id"], "tool": step["tool"], "status": "pending", "attempts": 0,
                              "permission_level": self.s.registry.get(step["tool"]).permission_level,
                              "verified": False, "undo_id": None} for step in plan["steps"]]}
        # A goal can contain arbitrary user text: redact credential-shaped assignments in history.
        session["goal"] = re.sub(r"(?i)(password|api[_ -]?key|token|secret)\s*[:=]\s*\S+", r"\1=[redacted]", session["goal"])
        from jarvix.capabilities.desktop import redact
        session["goal"] = redact(session["goal"])
        with self._lock:
            if _parent:
                parent = self.get(_parent)
                if parent.get("retry_session_id"):
                    raise ValueError("This plan already has a recovery session. Continue that session instead.")
                parent["retry_session_id"] = session_id
                self.s.records.put("operator_session", parent, _parent)
            self._active[session_id] = {"cancel": cancel, "resume": resumed}
            self._plans[session_id] = plan
        results = copy.deepcopy(_seed or {})
        mutations = set(_mutations or ())
        self._mutations[session_id] = mutations
        self._results[session_id] = results
        self._publish(session, event)
        deadline = min(context.deadline if context else float("inf"), time.monotonic() + plan.get("timeout_seconds", 120))
        previous_checkpoint = context.checkpoint if context else None

        def checkpoint():
            if previous_checkpoint:
                previous_checkpoint()
            if cancel.is_set() or time.monotonic() >= deadline:
                raise InterruptedError("Operator stopped.")
            if not resumed.is_set():
                session["status"] = "paused"
                self._publish(session, event)
                while not resumed.wait(.1):
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise InterruptedError("Operator stopped.")
                if cancel.is_set():
                    raise InterruptedError("Operator stopped.")
                session["status"] = "running"
                self._publish(session, event)

        try:
            checkpoint()
            preview = {"goal": plan["goal"], "steps": [{**step,
                "permission_level": self.s.registry.get(step["tool"]).permission_level} for step in plan["steps"]]}
            if not approval(PermissionRequest("execute", "operator.run", "operator.preview",
                    "Review this plan. Sensitive actions still ask again immediately before execution.",
                    {}, json.dumps(preview, ensure_ascii=False, indent=2))):
                session["status"] = "denied"
            else:
                session["status"] = "running"
                self._publish(session, event)
                with operation(cancel, approval, timeout=plan.get("timeout_seconds", 120), max_steps=64,
                               on_event=event, checkpoint=checkpoint):
                    for step, progress in zip(plan["steps"], session["steps"], strict=True):
                        check_cancelled()
                        progress["status"] = "running"
                        self._publish(session, event)
                        arguments = _resolve(step["arguments"], results)
                        writing = self.s.registry.get(step["tool"]).risk != "read"
                        if writing:
                            fingerprint = step["tool"] + json.dumps(arguments, sort_keys=True)
                            if fingerprint in mutations:
                                raise ValueError("Resolved arguments would repeat an earlier mutation.")
                            mutations.add(fingerprint)
                        for attempt in range(step.get("retries", 0) + 1):
                            check_cancelled()
                            progress["attempts"] = attempt + 1
                            progress["retry_safe"] = not writing
                            outcome = self.s.execute_tool(step["tool"], arguments, approve=approval, cancel=cancel, on_event=event)
                            if not outcome.ok and outcome.sensitivity == "public":
                                progress["retry_safe"] = True  # Execution was denied at the host boundary.
                                if writing:
                                    mutations.discard(fingerprint)
                            # Denials and uncertain mutations never qualify for automatic retry.
                            if outcome.ok or outcome.sensitivity == "public" or cancel.is_set():
                                break
                        results[step["id"]] = outcome.as_dict()
                        progress["undo_id"] = outcome.data.get("undo_id") if isinstance(outcome.data, dict) else None
                        failed = not self._result_ok(outcome)
                        progress["verified"] = self._intrinsic(step["tool"], outcome)
                        if not failed and step.get("expected"):
                            expected = step["expected"]
                            verified = self.s.execute_tool(expected["tool"], _resolve(expected["arguments"], results),
                                                           approve=approval, cancel=cancel, on_event=event)
                            matched = self._result_ok(verified)
                            if matched and "equals" in expected:
                                matched = _path(verified.as_dict(), expected.get("path", ["data"])) == expected["equals"]
                            progress["verified"] = bool(matched)
                            failed = not matched
                        check_cancelled()
                        progress["status"] = "failed" if failed else "complete"
                        if failed:
                            progress["error"] = "Action or verification failed. Inspect the current state before retrying."
                            session["status"] = "failed"
                        elif not progress["verified"]:
                            progress["verification"] = "Tool reported completion; no observable postcondition was supplied."
                        self._publish(session, event)
                        if failed:
                            break
                    else:
                        session["status"] = "complete"
        except InterruptedError:
            session["status"] = "cancelled" if cancel.is_set() else "timed_out"
        except Exception:
            session["status"] = "failed"
            session["error"] = "Plan stopped because a target, reference or verification became unavailable."
        finally:
            for step in session["steps"]:
                if step["status"] == "running":
                    step["status"] = session["status"]
                    step["error"] = "Interrupted action may have completed. Inspect state before retrying."
            self._publish(session, event)
            with self._lock:
                self._active.pop(session_id, None)
                # Bound retained private values to the last twenty plans; history stays in SQLite.
                while len(self._plans) > 20:
                    oldest = next((key for key in self._plans if key not in self._active), None)
                    if oldest is None:
                        break
                    self._plans.pop(oldest, None)
                    self._results.pop(oldest, None)
                    self._mutations.pop(oldest, None)
        response = self.get(session_id)
        response["ok"] = session["status"] == "complete"
        response["unverified_steps"] = [step["id"] for step in session["steps"] if step["status"] == "complete" and not step["verified"]]
        # ToolRegistry enforces the final response budget. Large private results are never persisted.
        response["results"] = results if len(json.dumps(results, default=str)) < 30000 else {"omitted": "Inspect individual tools locally."}
        return response

    def _intrinsic(self, tool, result):
        if not result.ok or not isinstance(result.data, dict):
            return False
        if "verified" in result.data:
            return result.data["verified"] is True
        if tool in {"notes.create", "tasks.create"}:
            table = "notes" if tool == "notes.create" else "tasks"
            return bool(self.s.db.query(f"SELECT id FROM {table} WHERE id=?", (result.data.get("id"),)))
        # Existing file operations hash and verify before returning a reversible operation receipt.
        return bool(tool.startswith("files.") and result.data.get("operation_id"))

    @staticmethod
    def _result_ok(result):
        if not result.ok:
            return False
        if isinstance(result.data, dict):
            if any(result.data.get(key) is False for key in ("ok", "completed", "matched")):
                return False
            if result.data.get("verified") is False and not result.data.get("requested"):
                return False
        return True

    def retry(self, id, approve=None, cancel=None, on_event=None):
        session = self.get(id)
        if session["status"] not in TERMINAL or id not in self._plans:
            raise ValueError("Only a stopped session from this app run can be retried. Re-plan older sessions.")
        if session.get("retry_session_id"):
            raise ValueError("This plan already has a recovery session. Continue that session instead.")
        remaining = {step["id"] for step in session["steps"] if step["status"] != "complete"}
        if not remaining:
            raise ValueError("There are no failed or pending steps.")
        if any(step["id"] in remaining and step.get("retry_safe") is False for step in session["steps"]):
            raise ValueError("A failed action may already have changed the computer. Inspect its state and create a new plan before repeating it.")
        plan = copy.deepcopy(self._plans[id])
        plan["steps"] = [step for step in plan["steps"] if step["id"] in remaining]
        seed = {key: value for key, value in self._results.get(id, {}).items() if key not in remaining}
        return self.run(plan, approve, cancel, on_event, _seed=seed, _parent=id,
                        _mutations=self._mutations.get(id))

    def undo(self, id, step_id, approve=None, cancel=None, on_event=None):
        session = self.get(id)
        if session["status"] not in TERMINAL:
            raise ValueError("Stop the session before undoing an action.")
        step = next((step for step in session["steps"] if step["id"] == step_id), None)
        if not step or not step.get("undo_id"):
            raise ValueError("This step has no supported undo.")
        result = self.s.execute_tool("actions.undo", {"id": step["undo_id"]}, approve, cancel, on_event)
        if result.ok:
            step["undo_id"] = None
            step["undone"] = True
            self._publish(session, on_event)
        return result.as_dict()

    def close(self):
        with self._lock:
            for active in self._active.values():
                active["cancel"].set()
                active["resume"].set()


def setup(s, registry):
    s.operator = service = OperatorService(s)
    register(registry, "operator.preview", "Validate and preview a bounded plan. Arguments may reference earlier results with {\"$ref\":\"step_id.data.id\"}.",
             {"plan": PLAN}, ("plan",), service.preview)
    def run(plan):
        value = service.run(plan)
        return ToolResult(value["ok"], value, None if value["ok"] else "Operator session stopped before completion.")
    register(registry, "operator.run", "Preview then execute a structured plan with live progress and per-action permissions. For each observable result supply expected read-only tool/path/equals. Do not call recursively.",
             {"plan": PLAN}, ("plan",), run, risk="write")
    register(registry, "operator.sessions", "List local operator session history without private tool values.", {}, (), service.list)
    register(registry, "operator.session", "Inspect session steps, permissions, outcomes and supported undo receipts.", {"id": ID}, ("id",), service.get)
    for name in ("pause", "resume", "cancel"):
        register(registry, f"operator.{name}", f"{name.title()} an active operator session.", {"id": ID}, ("id",), getattr(service, name), 2)
    register(registry, "operator.retry", "Review and retry reads, denied actions and pending steps. Uncertain mutations require inspection and a new plan.",
             {"id": ID}, ("id",), service.retry, 3)
