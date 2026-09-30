"""Supervise bounded Operator work without duplicating its execution or permissions."""
from __future__ import annotations

import copy
import json
import threading
import time

from jarvix.capabilities.operator import PLAN, TERMINAL
from jarvix.capabilities.operator_graph import graph
from jarvix.capabilities.schema import BOOL, ID, register
from jarvix.domain import PermissionRequest
from jarvix.runtime import CURRENT, check_cancelled, operation

FAILURE_CLASSES = {
    "permission_denied": "permission", "invalid_arguments": "validation", "invalid_result": "validation",
    "step_deadline": "timeout", "verification_failed": "verification", "dependency_failed": "dependency",
    "recovery_required": "uncertain_state", "target_unavailable": "availability", "tool_failed": "tool",
    "process_interrupted": "interruption",
}


class ExecutionSupervisor:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._workers = {}
        self._closed = False
        self._approval_handler = None

    def set_approval_handler(self, callback):
        """A persistent UI broker owns detached confirmations, not the launch worker."""
        if callback is not None and not callable(callback):
            raise ValueError("Approval handler must be callable.")
        with self._lock:
            self._approval_handler = callback

    def inspect(self, id):
        row = self.s.operator.get(id)
        steps = copy.deepcopy(row["steps"])
        for step in steps:
            step["failure_class"] = FAILURE_CLASSES.get(step.get("failure_code")) or {
                "timed_out": "timeout", "cancelled": "cancellation", "interrupted": "interruption",
            }.get(step["status"])
        result = {**row, "steps": steps, "graph": graph({"steps": steps}, row.get("retained_step_ids", []))}
        result["restart_resume_available"] = (row.get("checkpoint_available", False)
            and row["status"] in TERMINAL - {"complete"} and row.get("retry_available", True))
        return result

    def list(self):
        return [self.inspect(row["id"]) for row in self.s.operator.list()]

    def recoverable(self):
        return [row for row in self.list() if row["restart_resume_available"] and not row.get("retry_session_id")]

    def start(self, plan, background=False, approve=None, on_event=None):
        """Explicit user-reviewed task; detached work has its own cancel token."""
        if type(background) is not bool:
            raise ValueError("Background must be a boolean.")
        check_cancelled()
        validated = self.s.operator._validate(plan)
        if background:
            self.s.operator.background_safe(validated)
        current = CURRENT.get()
        if current and current.unattended:
            raise PermissionError("Unattended work cannot start independent Operator sessions.")
        approve = approve or (current.approve if current else lambda _: False)
        on_event = on_event or (current.on_event if current else None)
        preview = self.s.operator.preview(validated)
        approved_preview = {"goal": validated["goal"], "on_failure": validated.get("on_failure", "stop"),
                            "steps": [{**step, "permission_level": self.s.registry.get(step["tool"]).permission_level}
                                      for step in validated["steps"]]}
        if not approve(PermissionRequest("execute", "execution.start", "operator.preview",
                "Start this supervised task. Sensitive actions still require fresh confirmation.",
                {"background": background}, json.dumps(preview, ensure_ascii=False, indent=2))):
            return {"accepted": False, "reason": "Task preview was not approved."}
        check_cancelled()
        ready, cancel = threading.Event(), threading.Event()
        value = {}

        def event(kind, data):
            if kind == "operator_session":
                value["id"] = data["id"]
                if background and data["status"] == "running" and data.get("execution_mode") != "background":
                    self.s.operator.handoff(data["id"], True)
                ready.set()
            if on_event:
                try:
                    on_event(kind, data)
                except Exception:
                    pass  # A detached observer cannot turn safe work into a failed action.

        # Only the identical internal plan preview reuses this one approval.
        # No per-action sensitive approval is cached or forwarded in background.
        preview_available = True
        def approval(request):
            nonlocal preview_available
            if preview_available and request.tool_name == "operator.run" and request.permission == "operator.preview":
                try:
                    matches = json.loads(request.preview) == approved_preview
                except (ValueError, TypeError):
                    matches = False
                if matches:
                    preview_available = False
                    return True
            if background or cancel.is_set():
                return False
            with self._lock:
                handler, closed = self._approval_handler, self._closed
            if closed:
                return False
            try:
                answer = handler(request, cancel) if handler else approve(request)
                return bool(answer) and not cancel.is_set()
            except Exception:
                return False  # A missing/deleted approval surface never authorizes an action.

        def work():
            try:
                with operation(cancel, approval, timeout=validated.get("timeout_seconds", 120), max_steps=64,
                               on_event=event):
                    self.s.operator.run(validated, approve=approval, cancel=cancel, on_event=event)
            except Exception:
                self.s.repository.audit("error", "Supervised task could not initialize or stopped unexpectedly")
            finally:
                ready.set()
                with self._lock:
                    self._workers.pop(thread, None)

        with self._lock:
            if self._closed or len(self._workers) >= 2:
                raise ValueError("Supervisor is stopping or already supervising two tasks.")
            thread = threading.Thread(target=work, name="JarvixSupervisor", daemon=True)
            self._workers[thread] = cancel
            thread.start()
        ready.wait(2)
        if "id" not in value and not thread.is_alive():
            return {"accepted": False, "reason": "Task could not initialize; review current tool availability."}
        return {"accepted": True, "id": value.get("id"), "mode": "background" if background else "foreground"}

    def handoff(self, id, background):
        return self.s.operator.handoff(id, background)

    def resume_after_restart(self, id, approve=None, cancel=None, on_event=None):
        # Operator refreshes read observations and keeps write receipts; uncertain writes stop recovery.
        return self.s.operator.retry(id, approve=approve, cancel=cancel, on_event=on_event)

    def close(self, timeout=5):
        with self._lock:
            self._closed = True
            workers = list(self._workers.items())
            for _, cancel in workers:
                cancel.set()
        deadline = time.monotonic() + max(0, min(float(timeout), 30))
        for worker, _ in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=max(0, deadline - time.monotonic()))
        return not any(worker.is_alive() for worker, _ in workers)


def setup(s, registry):
    service = s.supervisor = ExecutionSupervisor(s)
    register(registry, "execution.start", "Review and start a bounded supervised Operator task. Background mode accepts only host-approved reads.",
             {"plan": PLAN, "background": BOOL}, ("plan",), service.start, 2)
    register(registry, "execution.sessions", "Inspect supervised progress, dependency graphs, failure classes and restart recovery.",
             {}, (), service.list)
    register(registry, "execution.session", "Inspect a task's public progress and dependency graph.", {"id": ID}, ("id",), service.inspect)
    register(registry, "execution.handoff", "Switch an active task between foreground and bounded read-only background work at its next checkpoint.",
             {"id": ID, "background": BOOL}, ("id", "background"), service.handoff, 2)
