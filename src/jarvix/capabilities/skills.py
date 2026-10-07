"""Reviewed reusable recipes backed by existing manual routines, never learned code."""
from __future__ import annotations

import copy
import json
import re
import threading
import time

from jarvix.browser_bridge import PRIVATE_WORDS
from jarvix.capabilities.desktop import SECRET
from jarvix.capabilities.operator import _resolve
from jarvix.capabilities.schema import BOOL, ID, array, enum, integer, register, schema, string
from jarvix.capabilities.workflows import BACKGROUND_OPT_IN, DEFINITION_FIELDS, fingerprint
from jarvix.runtime import CURRENT, check_cancelled, operation
from jarvix.services import required_text
from jarvix.storage import now_iso

DURABLE_WRITES = BACKGROUND_OPT_IN | frozenset({
    "files.rename", "files.recycle", "notes.edit", "notes.organize", "notes.export",
    "tasks.update", "tasks.complete", "tasks.reopen", "tasks.add_subtask", "tasks.from_note",
})
TRANSIENT_FAMILIES = ("desktop.", "adapters.", "browser.", "clipboard.", "windows.",
                      "processes.", "operator.", "workflows.", "routines.", "skills.",
                      "intelligence.", "models.", "developer.command_")
TRANSIENT_FIELDS = {"handle", "process_id", "element_ref", "ref", "tab_id", "session_id",
                    "operation_id", "screenshot_id", "root_id", "process_started"}
DECLARATION = schema({"fields": array(schema({"name": string(80),
    "type": enum("string", "number", "boolean", "object", "array"),
    "description": string(1000, 0), "required": BOOL}, ("name", "type")), 24),
    "description": string(2000, 0)})
METADATA = {"description": string(2000, 0), "instructions": string(8000, 0),
            "version": integer(1, 1000000), "input_schema": DECLARATION, "output_schema": DECLARATION}


def _metadata(target, supplied):
    """Declarations document a literal recipe; they never bind or interpolate inputs."""
    value = {"description": "", "instructions": "", "input_schema": {"fields": []},
             "output_schema": {"fields": []}, **{k: target[k] for k in METADATA if k in target}, **supplied}
    value["version"] = supplied.get("version", target.get("version", 0) + 1)
    if target and value["version"] <= target.get("version", 0):
        raise ValueError("An edited skill needs a newer version.")
    from jsonschema import Draft202012Validator
    if not Draft202012Validator(schema(METADATA)).is_valid(value):
        raise ValueError("Skill metadata does not match its declared format.")
    for field in ("input_schema", "output_schema"):
        names = [item["name"] for item in value[field].get("fields", [])]
        if len(set(names)) != len(names):
            raise ValueError("Declared field names must be unique.")
    if SECRET.search(json.dumps(value)):
        raise ValueError("Do not store credential-shaped values in skill metadata.")
    return copy.deepcopy(value)


class SkillService:
    def __init__(self, services):
        self.s = services
        self._reviews = {}
        self._lock = threading.RLock()

    def _read(self, name, arguments):
        if name not in self.s.enabled_tools():
            raise PermissionError("This skill source tool is disabled.")
        result = self.s.execute_tool(name, arguments)
        if not result.ok:
            raise PermissionError(result.error or "This skill source is unavailable.")
        return result.data

    def _arguments(self, value, tool):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref" or key in TRANSIENT_FIELDS or PRIVATE_WORDS.search(key):
                    raise ValueError("Skills require literal durable arguments without protected fields or transient references.")
                if key in {"path", "source", "destination", "cwd"}:
                    self.s.files.path(child, existing=key != "destination" and tool not in {"files.create_folder", "notes.export"})
                elif key in {"paths", "folders"}:
                    for path in child:
                        self.s.files.path(path)
                self._arguments(child, tool)
        elif isinstance(value, list):
            for child in value:
                self._arguments(child, tool)
        elif isinstance(value, str) and SECRET.search(value):
            raise ValueError("Credential-shaped values cannot be learned into a skill.")

    def _validate_recipe(self, definition, allow_commands=False):
        # ponytail: literal sequences only, up to 24 actions; add parameters when concrete recipes prove insufficient.
        if (definition.get("kind") != "routine" or definition.get("trigger") != "manual"
                or definition.get("variables") or definition.get("conditions")
                or not isinstance(definition.get("steps"), list) or not 1 <= len(definition["steps"]) <= 24):
            raise ValueError("Choose a manual routine with 1–24 literal actions and no inferred parameters or conditions.")
        if sum(step.get("tool") == "developer.command_start" for step in definition["steps"]) > 4:
            raise ValueError("Split an explicit command recipe into at most four separately reviewed commands.")
        enabled = set(self.s.enabled_tools())
        for step in definition["steps"]:
            check_cancelled()
            if step.get("kind") != "action":
                raise ValueError("Skills reuse concrete tool actions; edit advanced blocks in the original routine builder.")
            spec = self.s.registry.get(step["tool"])
            explicit_command = allow_commands and spec.name == "developer.command_start"
            if ((spec.name.startswith(TRANSIENT_FAMILIES) and not explicit_command) or spec.name not in enabled
                    or ((spec.risk != "read" or spec.permission_level == 3) and spec.name not in DURABLE_WRITES and not explicit_command)
                    or self.s.db.query("SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (spec.name,))):
                raise PermissionError("This recipe contains a disabled, protected, transient or unsupported action.")
            if self.s.registry.validate(spec.name, step["arguments"]):
                raise ValueError("Recipe arguments no longer match their registered tool.")
            if spec.name == "apps.open" and self.s.apps.arguments_for(step["arguments"]["id"]):
                raise PermissionError("Applications with executable command arguments cannot be learned into a skill.")
            self._arguments(step["arguments"], spec.name)
            if explicit_command:
                self._read("developer.command_preview", step["arguments"])

    def _routine(self, id, allow_commands=False):
        preview = self._read("workflows.preview", {"id": id})
        definition = {key: preview[key] for key in DEFINITION_FIELDS if key in preview}
        self._validate_recipe(definition, allow_commands)
        saved = self.s.records.get("workflow", id)
        if fingerprint(definition) != saved["approval_fingerprint"]:
            raise PermissionError("The routine changed without a fresh workflow review.")
        return definition, preview, saved["approval_fingerprint"]

    def _session_recipe(self, name, session_id):
        session = self._read("operator.session", {"id": session_id})
        steps = session.get("steps", [])
        if (session.get("status") != "complete" or not steps
                or any(step.get("status") != "complete" or step.get("verified") is not True
                       or step.get("undone") for step in steps)):
            raise ValueError("Only fully completed, verified and unchanged Operator work can be learned.")
        with self.s.operator._lock:
            if session_id in self.s.operator._plans:
                plan = copy.deepcopy(self.s.operator._plans[session_id])
                results = copy.deepcopy(self.s.operator._results.get(session_id, {}))
            else:
                checkpoint = self.s.operator.checkpoints.load(session_id)
                plan, results = checkpoint["plan"], checkpoint["results"]
        public = {step["id"]: step for step in steps}
        if set(public) != {step["id"] for step in plan["steps"]}:
            raise ValueError("The retained plan does not match the verified session.")
        actions = []
        for step in plan["steps"]:
            if step["tool"] != public[step["id"]]["tool"] or not results.get(step["id"], {}).get("ok"):
                raise ValueError("A verified source action is unavailable.")
            actions.append({"kind": "action", "tool": step["tool"],
                            "arguments": _resolve(step["arguments"], results), "id": step["id"]})
        return ({"name": name, "steps": actions, "kind": "routine", "trigger": "manual", "enabled": False},
                {"kind": "operator_session", "id": session_id, "fingerprint": fingerprint(session)})

    def _requirements(self, definition):
        specs = [self.s.registry.get(name) for name in dict.fromkeys(step["tool"] for step in definition["steps"])]
        from jarvix.capabilities.integration import INTEGRATIONS
        return {"required_permissions": [{"tool": spec.name, "permission": spec.permission,
                                           "level": spec.permission_level, "risk": spec.risk} for spec in specs],
                "required_integrations": sorted({spec.name.split(".")[0] for spec in specs
                                                 if spec.name.split(".")[0] in INTEGRATIONS})}

    def _prepare(self, name, session_id=None, routine_id=None, id=None, recipe=None, skill_id=None, **metadata):
        name = required_text(name, "Skill name", 160)
        if sum(source is not None for source in (session_id, routine_id, recipe, skill_id)) != 1:
            raise ValueError("Choose one verified Operator session, manual routine, saved skill or literal recipe.")
        if set(metadata) - set(METADATA):
            raise ValueError("Unsupported skill metadata.")
        target = self.s.records.get("skill", id) if id else None
        if skill_id:
            existing, _ = self._linked(skill_id)
            routine_id = existing["routine_id"]
            metadata = {**{key: existing[key] for key in METADATA if key in existing and key != "version"}, **metadata}
        if routine_id:
            definition, _, source_fingerprint = self._routine(routine_id, bool(skill_id and existing.get("explicit_commands")))
            source = {"kind": "skill" if skill_id else "routine", "id": skill_id or routine_id,
                      "fingerprint": fingerprint(existing) if skill_id else source_fingerprint}
            if skill_id:
                definition = {**definition, "name": name, "enabled": False, "approved_tools": []}
        elif recipe is not None:
            if (not isinstance(recipe, dict) or set(recipe) - set(DEFINITION_FIELDS)
                    or len(json.dumps(recipe, allow_nan=False)) > 32000):
                raise ValueError("Use a literal routine definition up to 32 KB.")
            definition = {**copy.deepcopy(recipe), "name": name, "kind": "routine", "trigger": "manual",
                          "enabled": False, "approved_tools": []}
            source = {"kind": "recipe", "fingerprint": fingerprint(recipe)}
        else:
            definition, source = self._session_recipe(name, session_id)
        explicit_commands = (recipe is not None or bool(skill_id and existing.get("explicit_commands")))
        explicit_commands = explicit_commands and any(step.get("tool") == "developer.command_start" for step in definition.get("steps", []))
        if session_id or recipe is not None or skill_id:
            self._validate_recipe(definition, explicit_commands)
            normalized = self._read("workflows.preview_definition", definition)
            definition = {key: normalized[key] for key in DEFINITION_FIELDS if key in normalized}
        review = {"name": name, **_metadata(target or {}, metadata), **self._requirements(definition),
                  "source": source, "routine": definition,
                  "explicit_commands": explicit_commands,
                  "command_previews": [self._read("developer.command_preview", step["arguments"])
                                       for step in definition["steps"] if step["tool"] == "developer.command_start"],
                  "target_fingerprint": fingerprint(target) if target else None}
        if len(json.dumps(review, allow_nan=False)) > 28000:
            raise ValueError("Split this skill into a smaller recipe; its exact review exceeds 28 KB.")
        return {**review, "review_fingerprint": fingerprint(review), "actions_executed": 0,
                "arguments_policy": "Concrete reviewed values are reused exactly; no parameters are inferred.",
                "verification_scope": "Runs use the existing routine's tool outcomes; prior Operator postconditions are not replayed.",
                "automatic_execution": False, "cloud_request": False}

    def learn_preview(self, name, session_id=None, routine_id=None, id=None, recipe=None, skill_id=None, **metadata):
        value = self._prepare(name, session_id, routine_id, id, recipe, skill_id, **metadata)
        with self._lock:
            self._reviews[value["review_fingerprint"]] = time.monotonic()
            while len(self._reviews) > 20:
                self._reviews.pop(next(iter(self._reviews)))
        return value

    def save_preview(self, name, review_fingerprint, session_id=None, routine_id=None, id=None, recipe=None, skill_id=None, **metadata):
        value = self._prepare(name, session_id, routine_id, id, recipe, skill_id, **metadata)
        with self._lock:
            reviewed_at = self._reviews.get(review_fingerprint)
            if (value["review_fingerprint"] != review_fingerprint or reviewed_at is None
                    or time.monotonic() - reviewed_at > 600):
                raise PermissionError("Preview this exact skill source again before saving; its review changed or expired.")
        return value

    def save(self, name, review_fingerprint, session_id=None, routine_id=None, id=None, recipe=None, skill_id=None, **metadata):
        with self._lock:
            preview = self.save_preview(name, review_fingerprint, session_id, routine_id, id, recipe, skill_id, **metadata)
            if session_id or recipe is not None or skill_id:
                saved = self._read("workflows.save", preview["routine"])
                routine_id = saved["id"]
            definition, _, recipe_fingerprint = self._routine(routine_id, preview["explicit_commands"])
            if definition != preview["routine"]:
                raise PermissionError("The routine changed during skill approval; inspect it and preview again.")
            value = {"name": preview["name"], "routine_id": routine_id, "recipe_fingerprint": recipe_fingerprint,
                     "source": preview["source"], "enabled": True,
                     "explicit_commands": preview["explicit_commands"],
                     **{key: preview[key] for key in (*METADATA, "required_permissions", "required_integrations")}}
            id = self.s.records.put("skill", value, id)
            self._reviews.pop(review_fingerprint, None)
        return {"id": id, **value, "actions_executed": 0, "automatic_execution": False}

    def list(self, query=""):
        query = query.casefold()
        rows = [row for row in self.s.records.list("skill") if query in row["name"].casefold()]
        return {"items": [{key: row.get(key) for key in ("id", "name", "routine_id", "enabled", "version", "source")}
                          for row in rows[:100]], "truncated": len(rows) > 100}

    def _linked(self, id):
        skill = self.s.records.get("skill", id)
        _, preview, current = self._routine(skill["routine_id"], skill.get("explicit_commands", False))
        if current != skill["recipe_fingerprint"]:
            raise PermissionError("The original routine changed; learn_preview and save its updated recipe before reuse.")
        return skill, preview

    def get(self, id):
        skill, routine = self._linked(id)
        history = self._read("workflows.history", {"id": skill["routine_id"]})
        runs = [row for row in history if not row.get("test_mode")]
        successes = sum(row.get("status") == "completed" and row.get("ok") is True for row in runs)
        failures = sum(row.get("status") in {"failed", "partial", "cancelled", "timed_out", "interrupted"} for row in runs)
        return {**skill, **self._requirements(routine), "routine": routine, "edit_tool": "routines.save",
                "edit_arguments": {"id": skill["routine_id"]},
                "input_policy": "Declared inputs and outputs are documentation; runs reuse exact literal arguments.",
                "command_previews": [self._read("developer.command_preview", step["arguments"])
                                     for step in routine["steps"] if step["tool"] == "developer.command_start"],
                "command_outcome_policy": "A command action records accepted start; inspect owned command_output and exit code to verify tests or builds passed.",
                "usage": {"source": "linked routine history", "recorded_runs": len(runs),
                          "scope": "Registered tool outcomes; command starts are not test/build success.",
                          "successes": successes, "failures": failures,
                          "success_rate": successes / (successes + failures) if successes + failures else None,
                          "last_status": runs[0]["status"] if runs else None, "history_limit": 100},
                "automatic_execution": False, "cloud_request": False}

    def preview(self, id):
        return {**self.get(id), "actions_executed": 0}

    def run(self, id, test_mode=False):
        if type(test_mode) is not bool:
            raise ValueError("Choose whether this run is a test.")
        skill, _ = self._linked(id)
        run_tool = "workflows.debug" if test_mode else "routines.run"
        if not skill["enabled"] or run_tool not in self.s.enabled_tools():
            raise PermissionError("This skill is disabled.")
        current = CURRENT.get()
        prior_checkpoint = current.checkpoint if current else None
        def checkpoint():
            if prior_checkpoint:
                prior_checkpoint()
            live = self.s.records.get("skill", id)
            if (not live["enabled"] or live["routine_id"] != skill["routine_id"]
                    or live["recipe_fingerprint"] != skill["recipe_fingerprint"]):
                raise PermissionError("This skill was disabled or changed; inspect it before continuing.")
        with operation(checkpoint=checkpoint):
            return self.s.execute_tool(run_tool, {"id": skill["routine_id"]})

    def export(self, id):
        skill, routine = self._linked(id)
        value = {"format": "jarvix.skill", "format_version": 1, "name": skill["name"],
                 "metadata": {key: skill[key] for key in METADATA if key in skill},
                 "recipe": {key: routine[key] for key in DEFINITION_FIELDS if key in routine}}
        if len(json.dumps(value, allow_nan=False)) > 40000:
            raise ValueError("This skill exceeds the portable 40 KB limit.")
        return {"payload": value, "exported_files": 0, "contains_literal_arguments": True,
                "automatic_execution": False}

    def import_preview(self, payload, name=None):
        if (not isinstance(payload, dict) or set(payload) != {"format", "format_version", "name", "metadata", "recipe"}
                or payload["format"] != "jarvix.skill" or type(payload["format_version"]) is not int or payload["format_version"] != 1
                or len(json.dumps(payload, allow_nan=False)) > 40000 or not isinstance(payload["metadata"], dict)
                or set(payload["metadata"]) - set(METADATA)):
            raise ValueError("Use a Jarvix skill export up to 40 KB.")
        args = {"name": name if name is not None else payload["name"], "recipe": payload["recipe"], **payload["metadata"]}
        preview = self.learn_preview(**args)
        return {**preview, "save_arguments": {**args, "review_fingerprint": preview["review_fingerprint"]},
                "imported": False, "permissions_imported": False}

    def patterns(self):
        """Explicit, bounded scan of saved verified work; never observes new app activity."""
        sessions = self._read("operator.sessions", {})
        eligible = [row for row in sessions if row.get("status") == "complete" and row.get("steps")
                    and all(step.get("status") == "complete" and step.get("verified") is True
                            and not step.get("undone") for step in row["steps"])]
        groups, skipped = {}, 0
        # ponytail: inspect at most 12 recent saved sessions; no background listener or inferred parameters.
        for session in eligible[:12]:
            try:
                recipe, _ = self._session_recipe("Repeated verified actions", session["id"])
                self._validate_recipe(recipe)
                actions = [{"tool": step["tool"], "arguments": step["arguments"]} for step in recipe["steps"]]
                key = fingerprint(actions)
                group = groups.setdefault(key, {"id": key, "tools": [step["tool"] for step in actions], "session_ids": []})
                group["session_ids"].append(session["id"])
            except (ValueError, OSError):
                skipped += 1
        preferences = {row["pattern_id"]: row for row in self.s.records.list("skill.pattern_preference")}
        items = []
        for group in groups.values():
            count = len(group["session_ids"])
            preference = preferences.get(group["id"], {})
            if count < 3 or preference.get("never_again") or count <= preference.get("dismissed_through", 0):
                continue
            items.append({**group, "frequency": count, "threshold": 3,
                          "reason": f"The same literal tool sequence completed and was verified in {count} saved Operator sessions.",
                          "conversion_requires_preview_and_confirmation": True})
        return {"items": items, "inspected_sessions": min(len(eligible), 12), "skipped_sources": skipped,
                "truncated": len(eligible) > 12, "actions_executed": 0, "skills_created": 0,
                "automatic_execution": False, "cloud_request": False}

    def pattern_preview(self, id, name):
        group = next((item for item in self.patterns()["items"] if item["id"] == id), None)
        if group is None:
            raise ValueError("This pattern is unavailable or dismissed; inspect current patterns first.")
        args = {"name": name, "session_id": group["session_ids"][0]}
        preview = self.learn_preview(**args)
        return {**preview, "pattern": group,
                "save_arguments": {**args, "review_fingerprint": preview["review_fingerprint"]}}

    def dismiss_pattern(self, id, never_again=False):
        if type(never_again) is not bool or not re.fullmatch(r"[a-f0-9]{64}", id):
            raise ValueError("Choose an existing pattern and dismissal setting.")
        with self._lock:
            group = next((item for item in self.patterns()["items"] if item["id"] == id), None)
            if group is None:
                raise ValueError("This pattern is no longer available.")
            existing = next((row for row in self.s.records.list("skill.pattern_preference") if row["pattern_id"] == id), None)
            self.s.records.put("skill.pattern_preference", {"pattern_id": id, "never_again": never_again,
                               "dismissed_through": group["frequency"], "dismissed_at": now_iso()},
                               existing["id"] if existing else None)
        return {"dismissed": True, "never_again": never_again, "actions_executed": 0}

    def set_enabled(self, id, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether this skill is enabled.")
        with self._lock:
            skill = self._linked(id)[0] if enabled else self.s.records.get("skill", id)
            skill["enabled"] = enabled
            self.s.records.put("skill", skill, id)
        return {"id": id, "enabled": enabled, "routine_preserved": True}

    def delete(self, id):
        with self._lock:
            self.s.records.get("skill", id)
            self.s.records.delete("skill", id)
        return {"deleted": True, "linked_routine_preserved": True}


def setup(s, registry):
    s.skills = service = SkillService(s)
    source = {"name": string(160), "session_id": ID, "routine_id": ID, "id": ID, "skill_id": ID,
              "recipe": {"type": "object", "maxProperties": 9}, **METADATA}
    register(registry, "skills.learn_preview", "Preview a literal skill from fully verified Operator work, an existing manual routine, a saved skill (duplicate) or an explicitly requested structured recipe. Metadata describes inputs/outputs without inferring arguments. Explicit commands need original fresh confirmation.",
             source, ("name",), service.learn_preview)
    register(registry, "skills.save", "Review the exact skill recipe and save only after fresh confirmation. Learned recipes reuse existing manual routines and never execute on save.",
             {**source, "review_fingerprint": string(64)}, ("name", "review_fingerprint"), service.save, 3)
    register(registry, "skills.list", "List explicitly saved skill names, sources and enabled state.",
             {"query": string(200, 0)}, (), service.list)
    for name in ("get", "preview"):
        register(registry, "skills." + name, "Inspect a skill's exact original routine, permissions and recorded success/failure usage. Sources are revalidated.",
                 {"id": ID}, ("id",), getattr(service, name))
    register(registry, "skills.run", "Freshly review then run a skill through its original manual routine. Every action retains its original permissions; no automatic execution.",
             {"id": ID, "test_mode": BOOL}, ("id",), service.run, 3)
    register(registry, "skills.export", "Return a bounded portable literal recipe and skill metadata. Contains exact reviewed arguments; does not write or upload files or export approvals.",
             {"id": ID}, ("id",), service.export)
    register(registry, "skills.import_preview", "Validate a portable skill and preview its literal recipe using current tools and file access. Returns exact skills.save arguments for separate fresh confirmation; never imports permissions or executes actions.",
             {"payload": {"type": "object", "maxProperties": 5}, "name": string(160)}, ("payload",), service.import_preview)
    register(registry, "skills.patterns", "Explicitly inspect at most 12 recent saved completed verified Operator sessions. Suggest only identical literal action sequences seen at least three times; no new app observation or silent skill creation.",
             {}, (), service.patterns)
    register(registry, "skills.pattern_preview", "Explicitly convert a currently available repeated verified pattern into a literal skill preview. Returns exact save arguments; saving still requires fresh review and confirmation.",
             {"id": string(64), "name": string(160)}, ("id", "name"), service.pattern_preview)
    register(registry, "skills.dismiss_pattern", "Dismiss a repeated-work suggestion, or never suggest that exact pattern again. Changes suggestion preferences only.",
             {"id": string(64), "never_again": BOOL}, ("id",), service.dismiss_pattern, 2)
    register(registry, "skills.set_enabled", "Enable or disable a saved skill without modifying or executing its original routine.",
             {"id": ID, "enabled": BOOL}, ("id", "enabled"), service.set_enabled, 3)
    register(registry, "skills.delete", "Delete a skill reference after confirmation; its original routine and history are preserved.",
             {"id": ID}, ("id",), service.delete, 3)
