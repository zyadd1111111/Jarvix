"""Bounded, previewed plans with persistent public progress and ephemeral private values."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace

from jsonschema import Draft202012Validator

from jarvix.capabilities.schema import ID, array, enum, integer, register, schema, string
from jarvix.domain import PermissionRequest, ToolResult
from jarvix.runtime import CURRENT, check_cancelled, operation
from jarvix.capabilities.checkpoints import CheckpointStore
from jarvix.capabilities.operator_graph import batches, ordered

EXPECTED = schema({"tool": string(80), "arguments": {"type": "object"},
                   "path": array({"type": ["string", "integer"]}, 12), "equals": {}}, ("tool", "arguments"))
STEP = schema({"id": string(60), "tool": string(80), "arguments": {"type": "object"},
               "expected": EXPECTED, "retries": integer(0, 1),
               "depends_on": {**array(string(60), 24), "uniqueItems": True}}, ("id", "tool", "arguments"))
PLAN = schema({"goal": string(500), "steps": {**array(STEP, 24), "minItems": 1},
               "timeout_seconds": integer(1, 600), "max_parallel_reads": integer(1, 3),
               "on_failure": enum("stop", "continue_independent_reads")}, ("goal", "steps"))
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
        self.checkpoints = CheckpointStore(self.s.records)
        for row in self.s.records.list("operator_session"):
            if row.get("status") not in TERMINAL:
                row["status"] = "interrupted"
                for step in row["steps"]:
                    if step["status"] == "running":
                        step["status"] = "interrupted"
                self.s.records.put("operator_session", row, row["id"])

    def set_callback(self, callback):
        self._listeners = [callback] if callback else []

    def _publish(self, session, event=None, stopped=None):
        with self._lock:
            if stopped is not None and stopped.is_set():
                return
            if session["id"] in self._plans:
                try:
                    self.checkpoints.save(session["id"], self._plans[session["id"]],
                                          self._results.get(session["id"], {}), self._mutations.get(session["id"], set()))
                    session["checkpoint_available"] = True
                except Exception:
                    # Never leave a stale checkpoint advertised as the current state.
                    self.checkpoints.delete(session["id"])
                    session["checkpoint_available"] = False
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
        plan = ordered(plan, seed or {})
        current = CURRENT.get()
        needed = sum(1 + step.get("retries", 0) + bool(step.get("expected")) for step in plan["steps"])
        if current and needed > current.steps_remaining:
            raise ValueError("This plan exceeds the remaining tool-step budget. Split it into smaller plans.")
        previous = set(seed or {})
        names = set()
        writes = set()
        enabled = set(self.s.enabled_tools())
        for step in plan["steps"]:
            if (not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,59}", step["id"])
                    or step["id"] in names or step["id"] in (seed or {})):
                raise ValueError("Step IDs must be unique, contain no dots and not replace retained results.")
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
                "on_failure": plan.get("on_failure", "stop"),
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

    def run(self, plan, approve=None, cancel=None, on_event=None, _seed=None, _parent=None, _mutations=None,
            _recovery=None):
        plan = self._validate(plan, _seed)
        context = CURRENT.get()
        approval = approve or (context.approve if context else lambda _: False)
        event = on_event or (context.on_event if context else None)
        cancel = cancel or (context.cancel if context else threading.Event())
        resumed = threading.Event()
        resumed.set()
        finished = threading.Event()
        session_id = self.s.repository.new_id()
        # No arguments, results, clipboard text, screenshots or typed values are kept in progress history.
        session = {"id": session_id, "goal": plan["goal"], "status": "awaiting_confirmation",
                   "parent_id": _parent, "timeout_seconds": plan.get("timeout_seconds", 120),
                   "on_failure": plan.get("on_failure", "stop"),
                   "retained_step_ids": sorted(_seed or {}),
                   "steps": [{"id": step["id"], "tool": step["tool"], "status": "pending", "attempts": 0,
                              "depends_on": step["depends_on"],
                              "permission_level": self.s.registry.get(step["tool"]).permission_level,
                              "verified": False, "undo_id": None} for step in plan["steps"]]}
        if _recovery:
            session["recovery_plan"] = copy.deepcopy(_recovery)
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
            if finished.is_set():
                raise InterruptedError("Operator session already stopped.")
            if previous_checkpoint:
                previous_checkpoint()
            if cancel.is_set() or time.monotonic() >= deadline:
                raise InterruptedError("Operator stopped.")
            if not resumed.is_set():
                session["status"] = "paused"
                self._publish(session, event, finished)
                while not resumed.wait(.1):
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise InterruptedError("Operator stopped.")
                if cancel.is_set():
                    raise InterruptedError("Operator stopped.")
                session["status"] = "running"
                self._publish(session, event, finished)

        def execute_step(step):
            progress = next(value for value in session["steps"] if value["id"] == step["id"])
            check_cancelled()
            writing = self.s.registry.get(step["tool"]).risk != "read"
            progress.update(status="running", retry_safe=True)
            self._publish(session, event, finished)
            arguments = _resolve(step["arguments"], results)
            if self.s.registry.validate(step["tool"], arguments):
                progress.update(status="failed", retry_safe=True, failure_code="invalid_arguments",
                                error="Resolved arguments do not match the tool schema.",
                                recovery="Inspect the source result and edit this step's arguments.")
                self._publish(session, event, finished)
                return False
            if writing:
                fingerprint = step["tool"] + json.dumps(arguments, sort_keys=True)
                if fingerprint in mutations:
                    # No execution was attempted by this step.
                    progress["retry_safe"] = True
                    raise ValueError("Resolved arguments would repeat an earlier mutation.")
                mutations.add(fingerprint)
                progress["retry_safe"] = False
                self._publish(session, event, finished)  # Persist intent before any side effect.
            for attempt in range(step.get("retries", 0) + 1):
                check_cancelled()
                if attempt:
                    if cancel.wait(.25):
                        raise InterruptedError("Operator stopped.")
                    check_cancelled()
                progress["attempts"] = attempt + 1
                outcome = self.s.execute_tool(step["tool"], arguments, approve=approval, cancel=cancel, on_event=event)
                # Independent readers may finish after the coordinator was stopped.
                # Their stale output must never overwrite a terminal session/checkpoint.
                if not writing:
                    check_cancelled()
                outcome, validation_failed = self._validate_result(step["tool"], outcome)
                if not outcome.ok and outcome.sensitivity == "public":
                    progress["retry_safe"] = True
                    if writing:
                        mutations.discard(fingerprint)
                if self._result_ok(outcome) or outcome.sensitivity == "public" or cancel.is_set() or validation_failed:
                    break
            with self._lock:
                if finished.is_set():
                    raise InterruptedError("Operator session already stopped.")
                results[step["id"]] = outcome.as_dict()
            progress["undo_id"] = outcome.data.get("undo_id") if isinstance(outcome.data, dict) else None
            failed = not self._result_ok(outcome)
            progress["verified"] = self._intrinsic(step["tool"], outcome)
            failure_code = ("invalid_result" if validation_failed else
                            "permission_denied" if outcome.sensitivity == "public" else "tool_failed")
            if not failed and step.get("expected"):
                expected = step["expected"]
                verified = self.s.execute_tool(expected["tool"], _resolve(expected["arguments"], results),
                                               approve=approval, cancel=cancel, on_event=event)
                verified, _ = self._validate_result(expected["tool"], verified)
                matched = self._result_ok(verified)
                if matched and "equals" in expected:
                    matched = _path(verified.as_dict(), expected.get("path", ["data"])) == expected["equals"]
                progress["verified"] = bool(matched)
                failed = not matched
                failure_code = "verification_failed"
            check_cancelled()
            progress["status"] = "failed" if failed else "complete"
            if failed:
                progress.update(error="Action or verification failed. Inspect the current state before retrying.",
                                failure_code=failure_code,
                                recovery="Review permissions and retry." if progress["retry_safe"] else
                                         "Inspect the changed target; create a new plan instead of replaying this action.")
            elif not progress["verified"]:
                progress["verification"] = "Tool reported completion; no observable postcondition was supplied."
            self._publish(session, event, finished)
            return not failed

        def guarded_step(step):
            try:
                return execute_step(step)
            except InterruptedError:
                raise
            except Exception:
                check_cancelled()
                progress = next(value for value in session["steps"] if value["id"] == step["id"])
                progress.update(status="failed", failure_code="target_unavailable",
                                error="A target, result reference or expected state became unavailable.",
                                recovery="Inspect the target and submit a changed recovery plan.")
                self._publish(session, event, finished)
                return False

        def parallel_step(step, child):
            token = CURRENT.set(child)
            try:
                return guarded_step(step)
            finally:
                CURRENT.reset(token)

        try:
            checkpoint()
            preview = {"goal": plan["goal"], "on_failure": plan.get("on_failure", "stop"), "steps": [{**step,
                "permission_level": self.s.registry.get(step["tool"]).permission_level} for step in plan["steps"]]}
            if _recovery:
                preview["recovery"] = _recovery
                preview["retained_step_ids"] = sorted(_seed or {})
            if not approval(PermissionRequest("execute", "operator.run", "operator.preview",
                    "Review this plan. Sensitive actions still ask again immediately before execution.",
                    {}, json.dumps(preview, ensure_ascii=False, indent=2))):
                session["status"] = "denied"
            else:
                session["status"] = "running"
                self._publish(session, event)
                with operation(cancel, approval, timeout=plan.get("timeout_seconds", 120), max_steps=64,
                               on_event=event, checkpoint=checkpoint):
                    failed_steps = set()
                    for batch in batches(plan, self.s.registry, results):
                        check_cancelled()
                        allowed = []
                        for step in batch:
                            blocked = sorted(set(step["depends_on"]) & failed_steps)
                            spec = self.s.registry.get(step["tool"])
                            safe_read = spec.risk == "read" and spec.permission_level == 1
                            if blocked or (failed_steps and not safe_read):
                                progress = next(value for value in session["steps"] if value["id"] == step["id"])
                                progress.update(status="skipped", retry_safe=True, blocked_by=blocked,
                                    failure_code="dependency_failed" if blocked else "recovery_required",
                                    error="A dependency failed." if blocked else "Further actions require a recovery plan.",
                                    recovery="Inspect the failed steps and review a changed plan.")
                                failed_steps.add(step["id"])
                                self._publish(session, event)
                            else:
                                allowed.append(step)
                        batch = allowed
                        if not batch:
                            continue
                        if len(batch) == 1:
                            outcomes = [guarded_step(batch[0])]
                        else:
                            parent = CURRENT.get()
                            budget = sum(1 + step.get("retries", 0) for step in batch)
                            if parent.steps_remaining < budget:
                                raise InterruptedError("Operator step budget exhausted.")
                            parent.steps_remaining -= budget
                            # Workers own separate execution contexts; budget is reserved
                            # once by the coordinator, never reset by parallel nested calls.
                            pool = ThreadPoolExecutor(max_workers=min(3, len(batch)), thread_name_prefix="JarvixRead")
                            try:
                                futures = [pool.submit(parallel_step, step, replace(parent,
                                    steps_remaining=1 + step.get("retries", 0), deadline=deadline)) for step in batch]
                                waiting = set(futures)
                                while waiting:
                                    check_cancelled()
                                    _, waiting = wait(waiting, timeout=.05, return_when=FIRST_COMPLETED)
                                outcomes = [future.result() for future in futures]
                            finally:
                                # Native reads cannot be forcibly killed, but they cannot hold
                                # the coordinator open or publish after cancellation/deadline.
                                pool.shutdown(wait=False, cancel_futures=True)
                        failed_steps.update(step["id"] for step, ok in zip(batch, outcomes, strict=True) if not ok)
                        if not all(outcomes) and plan.get("on_failure", "stop") == "stop":
                            session["status"] = "failed"
                            break
                    else:
                        session["status"] = "failed" if failed_steps else "complete"
        except InterruptedError:
            session["status"] = "cancelled" if cancel.is_set() else "timed_out"
        except Exception:
            session["status"] = "failed"
            session["error"] = "Plan stopped because a target, reference or verification became unavailable."
        finally:
            with self._lock:
                finished.set()
            for step in session["steps"]:
                if step["status"] == "running":
                    step["status"] = session["status"]
                    step["error"] = "Interrupted action may have completed. Inspect state before retrying."
            session["completed_count"] = sum(step["status"] == "complete" for step in session["steps"])
            session["partial_completion"] = session["completed_count"] > 0 and session["status"] != "complete"
            session["failed_step_ids"] = [step["id"] for step in session["steps"] if step["status"] == "failed"]
            session["skipped_step_ids"] = [step["id"] for step in session["steps"] if step["status"] == "skipped"]
            session["retry_available"] = (session["status"] in TERMINAL - {"complete"}
                and not any(step.get("retry_safe") is False for step in session["steps"] if step["status"] != "complete"))
            session["replan_available"] = session["status"] != "complete"
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
        if not isinstance(result, ToolResult) or result.ok is not True:
            return False
        if isinstance(result.data, dict):
            if any(result.data.get(key) is False for key in ("ok", "complete", "completed", "matched")):
                return False
            if result.data.get("verified") is False and not result.data.get("requested"):
                return False
        return True

    def _validate_result(self, tool, result):
        """Validate even substituted executors; private malformed values never enter checkpoints."""
        try:
            valid = (isinstance(result, ToolResult) and type(result.ok) is bool
                     and result.sensitivity in {"local", "public"}
                     and (result.error is None or isinstance(result.error, str))
                     and len(json.dumps(result.as_dict(), allow_nan=False)) <= 64000)
            result_schema = self.s.registry.get(tool).result_schema
            if valid and result.ok and result_schema:
                valid = Draft202012Validator(result_schema).is_valid(result.data)
        except Exception:
            valid = False
        if not valid:
            return ToolResult(False, error="Tool returned an invalid or oversized result."), True
        return result, False

    def _restore(self, id):
        session = self.get(id)
        if session["status"] not in TERMINAL:
            raise ValueError("Stop the active session before recovery.")
        if session.get("retry_session_id"):
            raise ValueError("This plan already has a recovery session. Continue that session instead.")
        if id not in self._plans:
            try:
                saved = self.checkpoints.load(id)
                self._plans[id], self._results[id] = saved["plan"], saved["results"]
                self._mutations[id] = set(saved["mutations"])
            except Exception:
                raise ValueError("No valid protected checkpoint is available. Re-plan this older session.") from None
        return session

    def _recovery(self, id, plan):
        session = self._restore(id)
        if session["status"] == "complete":
            raise ValueError("This session completed. Start a new plan for new work.")
        complete = {step["id"] for step in session["steps"]
                    if step["status"] == "complete" and not step.get("undone")}
        retained = complete | set(session.get("retained_step_ids", []))
        seed = {key: copy.deepcopy(value) for key, value in self._results[id].items() if key in retained}
        plan = self._validate(plan, seed)
        mutations = self._mutations.get(id, set())
        # Validate known fingerprints before preview; reference-dependent new values
        # get the same check immediately before execution in run().
        for step in plan["steps"]:
            if self.s.registry.get(step["tool"]).risk != "read":
                try:
                    arguments = _resolve(step["arguments"], seed)
                except (KeyError, IndexError):
                    continue
                if step["tool"] + json.dumps(arguments, sort_keys=True) in mutations:
                    raise ValueError("Recovery cannot replay a completed or uncertain mutation. Inspect its state first.")
        # A denied or failed recovery cannot erase uncertainty from its parent.
        # Keep requiring fresh observation until the operation is completed.
        uncertain = sorted(set(session.get("recovery_plan", {}).get("uncertain_step_ids", [])) | {
            step["id"] for step in session["steps"]
            if step.get("retry_safe") is False and step["status"] != "complete"})
        changes = {"source_session_id": id, "kind": "changed_plan", "retained_step_ids": sorted(seed),
                   "replaced_step_ids": [step["id"] for step in session["steps"] if step["id"] not in complete],
                   "uncertain_step_ids": uncertain,
                   "guidance": "Completed actions are retained. Inspect uncertain targets before further changes."}
        if uncertain:
            # Re-observation is mandatory after an uncertain write. Every new
            # mutation must descend from a fresh read in this changed plan.
            observed = set()
            for step in plan["steps"]:
                spec = self.s.registry.get(step["tool"])
                if spec.risk == "read" and spec.permission_level == 1:
                    observed.add(step["id"])
                elif not set(step["depends_on"]) & observed:
                    raise ValueError("Recovery actions must depend on a fresh read that inspects the uncertain target.")
                else:
                    observed.add(step["id"])
        return plan, seed, changes

    def preview_replan(self, id, plan):
        plan, _, changes = self._recovery(id, plan)
        return {"goal": plan["goal"], "recovery": changes,
                "on_failure": plan.get("on_failure", "stop"),
                "steps": [{**step, "permission_level": self.s.registry.get(step["tool"]).permission_level}
                          for step in plan["steps"]]}

    def replan(self, id, plan, approve=None, cancel=None, on_event=None):
        plan, seed, changes = self._recovery(id, plan)
        return self.run(plan, approve, cancel, on_event, _seed=seed, _parent=id,
                        _mutations=self._mutations.get(id), _recovery=changes)

    def retry(self, id, approve=None, cancel=None, on_event=None):
        restarted = id not in self._plans
        session = self._restore(id)
        if any(step.get("undone") for step in session["steps"]):
            raise ValueError("A completed action was undone. Inspect current state and submit a changed recovery plan.")
        remaining = {step["id"] for step in session["steps"] if step["status"] != "complete"}
        if not remaining:
            raise ValueError("There are no failed or pending steps.")
        if any(step["id"] in remaining and step.get("retry_safe") is False for step in session["steps"]):
            raise ValueError("A failed action may already have changed the computer. Inspect its state and create a new plan before repeating it.")
        if restarted:
            # Fresh reads replace stale observations. Mutations and their receipts
            # are reused, never repeated merely because the app was restarted.
            remaining |= {step["id"] for step in session["steps"]
                          if self.s.registry.get(step["tool"]).risk == "read"}
        plan = copy.deepcopy(self._plans[id])
        plan["steps"] = [step for step in plan["steps"] if step["id"] in remaining]
        seed = {key: value for key, value in self._results.get(id, {}).items() if key not in remaining}
        return self.run(plan, approve, cancel, on_event, _seed=seed, _parent=id,
                        _mutations=self._mutations.get(id))

    def forget_checkpoint(self, id):
        if id in self._active:
            raise ValueError("Stop the active session before removing its checkpoint.")
        self.checkpoints.delete(id)
        self._plans.pop(id, None)
        self._results.pop(id, None)
        self._mutations.pop(id, None)
        session = self.get(id)
        session["checkpoint_available"] = False
        self.s.records.put("operator_session", session, id)
        return {"removed": True, "history_retained": True}

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
    def retry(id):
        value = service.retry(id)
        return ToolResult(value["ok"], value, None if value["ok"] else "Operator retry stopped before completion.")
    register(registry, "operator.retry", "Review and retry reads, denied actions and pending steps. Uncertain mutations require inspection and a new plan.",
             {"id": ID}, ("id",), retry, 3)
    register(registry, "operator.preview_replan", "Preview a changed recovery plan after failure. Retain completed results via $ref; never replay completed or uncertain mutations.",
             {"id": ID, "plan": PLAN}, ("id", "plan"), service.preview_replan)
    def replan(id, plan):
        value = service.replan(id, plan)
        return ToolResult(value["ok"], value, None if value["ok"] else "Recovery plan stopped before completion.")
    register(registry, "operator.replan", "Execute a changed recovery plan after fresh preview confirmation. Reuse successful outputs and inspect uncertain targets before changes; each tool retains its permissions.",
             {"id": ID, "plan": PLAN}, ("id", "plan"), replan, risk="write")
    register(registry, "operator.forget_checkpoint", "Remove protected private recovery data for a stopped session, retaining its public action timeline.",
             {"id": ID}, ("id",), service.forget_checkpoint, 3)
