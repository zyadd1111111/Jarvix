"""Observed outcome summaries and routing preferences; never stores model reasoning."""
from __future__ import annotations

from collections import Counter
import math

from jarvix.capabilities.schema import BOOL, enum, integer, register, schema
from jarvix.model_router import ROUTING_PROFILES
from jarvix.runtime import check_cancelled

CUSTOM = schema({"prefer_local": BOOL, "prefer_cloud": BOOL,
                 "cost_preference": enum("balanced", "low"), "latency_preference": enum("balanced", "low")})
TERMINAL = {"complete", "failed", "partial", "denied", "cancelled", "timed_out", "interrupted"}
FAILURES = {"process_interrupted", "invalid_arguments", "invalid_result", "permission_denied", "tool_failed",
            "verification_failed", "step_deadline", "target_unavailable", "dependency_failed", "recovery_required",
            "deadline_exceeded", "cancelled", "collection_limit", "reference_unavailable"}


class EvaluationService:
    def __init__(self, services):
        self.s = services

    def _permitted(self, *names):
        return all(name in self.s.enabled_tools() and not self.s.db.query(
            "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,)) for name in names)

    def routing_options(self):
        profile = self.s.settings.get("routing.profile", "balanced")
        if profile not in ROUTING_PROFILES:
            profile = "balanced"
        options = {"prefer_local": self.s.settings.get("routing.prefer_local", True),
                   "prefer_cloud": self.s.settings.get("routing.prefer_cloud", False),
                   "cost_preference": self.s.settings.get("routing.cost_preference", "balanced"),
                   "latency_preference": self.s.settings.get("routing.latency_preference", "balanced")}
        if profile == "custom":
            options.update(self.s.settings.get("routing.custom", {}))
        if profile == "fast":
            options["latency_preference"] = "low"
        if profile == "local_only":
            options.update(prefer_local=True, prefer_cloud=False)
        return {**options, "profile": profile,
                "local_only": self.s.settings.get("ai.local_only", False) or profile == "local_only"}

    def profile(self):
        return {"profiles": list(ROUTING_PROFILES), "effective": self.routing_options(),
                "custom": self.s.settings.get("routing.custom", {}),
                "cloud_fallback_requires_disclosure": True,
                "quality_basis": "Observed failures and reported context capacity; no benchmark quality score."}

    def configure_profile(self, profile, custom=None):
        if profile not in ROUTING_PROFILES:
            raise ValueError("Choose a supported routing profile.")
        if custom is not None:
            if (profile != "custom" or not isinstance(custom, dict) or set(custom) - set(CUSTOM["properties"])
                    or self.s.registry.validate("models.configure_profile", {"profile": profile, "custom": custom})):
                raise ValueError("Use bounded custom routing preferences.")
            options = {"prefer_local": True, "prefer_cloud": False,
                       "cost_preference": "balanced", "latency_preference": "balanced", **custom}
            if options["prefer_local"] and options["prefer_cloud"]:
                raise ValueError("Choose either a local or a cloud preference.")
            self.s.settings.set("routing.custom", options)
        self.s.settings.set("routing.profile", profile)
        return self.profile()

    def report(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Inspect 1–500 recent saved outcomes per source.")
        if not self._permitted("evaluation.report"):
            raise PermissionError("Evaluation access is disabled or denied.")
        sources = {}
        for key, kind, tools in (
                ("operator", "operator_session", ("operator.sessions", "operator.session")),
                ("workflows", "workflow_run", ("workflows.history",)),
                ("tools", "action_history", ("activity.search",)),
                ("models", "model_outcome", ("models.health",))):
            check_cancelled()
            if not self._permitted(*tools):
                sources[key] = {"status": "permission_denied", "sample_count": 0}
                continue
            rows = self.s.records.list(kind)[:limit]
            if key == "models":
                items = [{field: row.get(field) for field in (
                    "provider", "model", "successes", "failures", "consecutive_failures", "latency_ms", "last_result")}
                    for row in rows]
                for item in items:
                    completed, failed = item.get("successes") or 0, item.get("failures") or 0
                    item["completion_rate"] = round(completed / (completed + failed), 3) if completed + failed else None
                sources[key] = {"status": "ok", "sample_count": len(rows), "items": items,
                                "latency_basis": "Saved exponential moving average; provider completion is not verified task success."}
                continue
            if key == "tools":
                counts = {}
                for row in rows:
                    name = row.get("tool", "")
                    if not self._permitted(name):
                        continue
                    item = counts.setdefault(name, {"tool": name, "completed": 0, "failed": 0,
                                                   "latency_samples": 0, "total_latency_ms": 0})
                    item["completed" if row.get("ok") is True else "failed"] += 1
                    latency = row.get("latency_ms")
                    if type(latency) in {int, float} and math.isfinite(latency) and 0 <= latency <= 86_400_000:
                        item["latency_samples"] += 1
                        item["total_latency_ms"] += latency
                for item in counts.values():
                    total = item.pop("total_latency_ms")
                    item["mean_latency_ms"] = round(total / item["latency_samples"], 2) if item["latency_samples"] else None
                    item["completion_rate"] = round(item["completed"] / (item["completed"] + item["failed"]), 3)
                sources[key] = {"status": "ok", "sample_count": sum(i["completed"] + i["failed"] for i in counts.values()),
                                "items": list(counts.values()), "verification": "Tool completion only; no postcondition inferred."}
                continue
            real = [row for row in rows if not row.get("test_mode")]
            terminal = [row for row in real if row.get("status") in TERMINAL]
            states = Counter(row.get("status", "unknown") for row in real)
            steps = [step for row in real for step in row.get("steps", [])]
            verified = sum(bool(row.get("steps")) and row.get("status") == "complete" and all(
                step.get("status") == "complete" and step.get("verified") is True and not step.get("undone")
                for step in row["steps"]) for row in terminal)
            retry_steps = [step for step in steps if step.get("attempts", 0) > 1]
            failures = Counter(step.get("failure_code") if step.get("failure_code") in FAILURES else "unknown" for step in steps
                               if step.get("status") in {"failed", "interrupted", "timed_out", "denied"})
            sources[key] = {"status": "ok", "sample_count": len(real), "test_runs_excluded": len(rows) - len(real),
                "states": dict(states), "terminal_count": len(terminal), "verified_complete": verified,
                "verified_completion_rate": round(verified / len(terminal), 3) if terminal else None,
                "reported_complete": sum(row.get("status") == "complete" for row in terminal),
                "retried_steps": len(retry_steps),
                "retries_reported_complete": sum(step.get("status") == "complete" for step in retry_steps),
                "uncertain_steps": sum(step.get("retry_safe") is False and step.get("status") != "complete" for step in steps),
                "failure_classes": dict(failures)}
        return {"sources": sources, "coverage": "Bounded saved outcome metadata; absent metrics are unknown, not success.",
                "fallback_count": None, "fallback_coverage": "Existing model counters do not identify fallback attempts.",
                "automatic_tuning": False, "cloud_request": False, "reasoning_stored": False}


def setup(s, registry):
    s.evaluation = service = EvaluationService(s)
    register(registry, "evaluation.report", "Summarize bounded saved task, retry, tool and model outcomes without private result values.",
             {"limit": integer(1, 500)}, (), service.report)
    register(registry, "models.profile", "Inspect routing profiles and effective privacy preferences.", {}, (), service.profile)
    register(registry, "models.configure_profile", "Choose routing preferences; local-only and cloud disclosure rules remain enforced.",
             {"profile": enum(*ROUTING_PROFILES), "custom": CUSTOM}, ("profile",), service.configure_profile, 3)
