"""Cross-app plan templates and account briefings composed through existing tools.

Templates are structured starting points for the planner, not phrase matching.
Briefings extract source evidence locally; they never invoke a cloud model.
"""
from __future__ import annotations

import json
import re

from jarvix.capabilities.evidence import terms
from jarvix.capabilities.schema import ID, array, enum, integer, register, string
from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled


class IntelligenceService:
    def __init__(self, services):
        self.s = services

    def _read(self, name, args):
        check_cancelled()
        if name not in self.s.enabled_tools():
            return ToolResult(False, error="This source tool is disabled.")
        result = self.s.execute_tool(name, args)
        if not result.ok:
            return result
        return result.data

    def prepare(self, project_id=None, goal="Continue this project", mission_id=None):
        """Resolve explicit references, then suggest only observations needed now."""
        goal = goal.strip() if isinstance(goal, str) else ""
        if not goal or len(goal) > 500:
            raise ValueError("Use a goal of 1–500 characters.")
        mission = None
        if mission_id:
            result = self._read("missions.summary", {"id": mission_id})
            if isinstance(result, ToolResult):
                return result
            mission = result
            project_id = project_id or result.get("project_id")
        context = None
        if self.s.settings.get("context.enabled", False):
            result = self._read("context.inspect", {})
            if isinstance(result, ToolResult):
                if not project_id:
                    return result
            else:
                context = result
        if not project_id:
            project_id = (context or {}).get("project", {}).get("id") if (context or {}).get("project") else None
        projects = self._read("projects.list", {})
        if isinstance(projects, ToolResult):
            return projects
        project = next((p for p in projects["items"] if p["id"] == project_id), None)
        if not project:
            raise ValueError("Select a project or a Mission with a linked project first.")
        root = str(self.s.files.path(project["path"]))
        observation = self._read("developer.project_inspect", {"path": root})
        if isinstance(observation, ToolResult):
            return observation
        graph = self._read("context.graph", {"kind": "project", "reference": project_id, "depth": 2, "limit": 20})
        if isinstance(graph, ToolResult):
            return graph
        linked_sessions = list(dict.fromkeys([
            *(item["id"] for item in (mission or {}).get("operator_sessions", [])),
            *(node["reference"] for node in graph.get("nodes", [])
              if node.get("kind") == "operator_session" and node.get("available"))]))
        sessions = []
        for id in linked_sessions[:3]:
            value = self._read("operator.session", {"id": id})
            if not isinstance(value, ToolResult):
                sessions.append({key: value.get(key) for key in ("id", "goal", "status", "progress_percent", "retry_available")})
        steps = []
        if observation.get("git_repository"):
            steps.append({"id": "git", "tool": "developer.git_status", "arguments": {"path": root}, "depends_on": []})
        steps.extend([
            {"id": "tasks", "tool": "tasks.search", "arguments": {"project_id": project_id, "view": "open"}, "depends_on": []},
            {"id": "files", "tool": "files.recent", "arguments": {"path": root, "limit": 5}, "depends_on": []},
            {"id": "knowledge", "tool": "knowledge_spaces.list", "arguments": {"project_id": project_id}, "depends_on": []},
        ])
        steps = [step for step in steps if step["tool"] in self.s.enabled_tools() and not self.s.db.query(
            "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (step["tool"],))]
        if not steps:
            return ToolResult(False, error="Enable an allowed project observation tool before preparing a continuation plan.")
        plan = {"goal": goal, "steps": steps, "timeout_seconds": 180, "max_parallel_reads": 2}
        preview = self._read("operator.preview", {"plan": plan})
        if isinstance(preview, ToolResult):
            return preview
        return {"project": project, "observation": observation, "mission": mission, "graph": graph,
                "context": context, "recent_sessions": sessions, "plan": plan, "preview": preview, "executed": False,
                "cloud_request": False, "recovery": "Inspect linked unfinished sessions and use their existing recovery previews. Never replay uncertain actions.",
                "next": "Run the reviewed observation plan with operator.run; use its outputs to choose the next useful action."}

    def handoff(self, role, steps, state=None):
        """Small read specialists exchange structured tool results through the host."""
        families = {"research": {"search", "browser", "github", "intelligence"},
                    "coding": {"developer", "files", "projects"},
                    "document": {"documents", "knowledge_spaces", "notes", "search"},
                    "desktop": {"desktop", "windows", "adapters", "apps"},
                    "automation": {"workflows", "routines", "execution"},
                    "planner": {"missions", "context", "operator", "projects", "tasks"}}
        if role not in families or not isinstance(steps, list) or not 1 <= len(steps) <= 5:
            raise ValueError("Choose a specialist and 1–5 bounded read steps.")
        from jarvix.capabilities.operator import _references, _resolve
        try:
            results = {} if state is None else json.loads(json.dumps(state, allow_nan=False))
        except (TypeError, ValueError):
            raise ValueError("Handoff state must contain bounded JSON results.") from None
        if (not isinstance(results, dict) or len(results) + len(steps) > 8 or len(json.dumps(results)) > 32000
                or any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,59}", k) or not isinstance(value, dict)
                       for k, value in results.items())):
            raise ValueError("Handoff state must be a small named JSON result map.")
        seen = set(results)
        # Validate all targets before any specialist read. No nested orchestration.
        for step in steps:
            if (not isinstance(step, dict) or set(step) != {"id", "tool", "arguments"}
                    or not isinstance(step["id"], str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,59}", step["id"])
                    or step["id"] in seen or not isinstance(step["arguments"], dict)):
                raise ValueError("Use unique step IDs and structured registered-tool arguments.")
            spec = self.s.registry.get(step["tool"])
            if (spec.permission_level not in {None, 1} or spec.risk != "read"
                    or step["tool"].split(".")[0] not in families[role]
                    or step["tool"] in {"intelligence.handoff", "intelligence.prepare", "intelligence.plan"}
                    or step["tool"] not in self.s.enabled_tools()
                    or self.s.db.query("SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (step["tool"],))):
                raise PermissionError("This specialist accepts enabled read-only tools from its declared families.")
            if not _references(step["arguments"], seen) and self.s.registry.validate(step["tool"], step["arguments"]):
                raise ValueError("Handoff arguments do not match the registered tool.")
            seen.add(step["id"])
        completed = []
        for step in steps:
            check_cancelled()
            if step["tool"] not in self.s.enabled_tools():
                return ToolResult(False, {"role": role, "completed": completed}, "A handoff source was disabled.")
            try:
                args = _resolve(step["arguments"], results)
            except (KeyError, ValueError, IndexError, TypeError):
                return ToolResult(False, {"role": role, "completed": completed}, "A handoff reference is unavailable.")
            result = self.s.execute_tool(step["tool"], args)
            if not result.ok:
                return ToolResult(False, {"role": role, "completed": completed, "failed_step": step["id"]}, result.error)
            value = result.as_dict()
            try:
                encoded = json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                return ToolResult(False, {"role": role, "completed": completed}, "Handoff reads must return structured JSON results.")
            if len(encoded) > 32000:
                return ToolResult(False, {"role": role, "completed": completed}, "Narrow this read before handing off its result.")
            results[step["id"]] = value
            if len(json.dumps(results, allow_nan=False)) > 48000:
                return ToolResult(False, {"role": role, "completed": completed}, "Handoff state limit reached; narrow the reads.")
            completed.append({"id": step["id"], "tool": step["tool"], "ok": True})
        return {"role": role, "completed": completed, "state": results, "cloud_request": False,
                "mode": "structured_read_handoff", "agents_started": 0}

    def briefing(self, source, query="", owner="", repo="", start="", end="", channel_id="", limit=5):
        # Every nested read goes through the facade, including per-tool denies.
        citations, lines = [], []
        if source == "gmail":
            found = self._read("gmail.search", {"query": query or "is:unread is:important", "limit": limit})
            if isinstance(found, ToolResult):
                return found
            for record in found.get("messages", [])[:limit]:
                mail = self._read("gmail.read", {"message_id": record["id"]})
                if isinstance(mail, ToolResult):
                    return mail
                headers = {h["name"].casefold(): h.get("value", "") for h in mail.get("headers", [])}
                title = headers.get("subject", "Untitled email")[:300]
                citations.append({"source": "gmail", "id": record["id"], "title": title})
                lines.append(title + "\n" + (mail.get("text") or mail.get("snippet") or "")[:1400])
        else:
            name, args, key = {
                "github": ("github.issues", {"owner": owner, "repo": repo, "state": "open", "limit": limit}, None),
                "calendar": ("google_calendar.events", {"start": start, "end": end, "query": query, "limit": limit}, "items"),
                "drive": ("google_drive.search", {"query": query or "trashed = false", "limit": limit}, "files"),
                "discord": ("discord.messages", {"channel_id": channel_id, "limit": limit}, None),
                "spotify": ("spotify.playback", {}, "playback"),
            }[source]
            value = self._read(name, args)
            if isinstance(value, ToolResult):
                return value
            records = ([value["item"]] if value.get("item") else []) if source == "spotify" else value.get(key, []) if key else value
            if not isinstance(records, list):
                return ToolResult(False, error="The account returned an unexpected listing.")
            for row in records[:limit]:
                if source == "github" and row.get("pull_request"):
                    continue
                title = str(row.get("title") or row.get("summary") or row.get("name") or row.get("id") or source)[:300]
                body = str(row.get("body") or row.get("description") or row.get("content") or "")[:1400]
                if source in {"github", "discord"} and query and not terms(query) & terms(title + " " + body):
                    continue
                if source == "calendar":
                    body = str(row.get("start", {})) + " → " + str(row.get("end", {})) + "\n" + body
                citations.append({"source": source, "id": str(row.get("id", row.get("number", ""))),
                                  "title": title})
                lines.append(title + "\n" + body)
        sections = [{"citation": citation, "text": text} for citation, text in zip(citations, lines, strict=True)]
        return {"source": source, "sections": sections,
                "text": "\n\n".join(f"[{i}] {text}" for i, text in enumerate(lines, 1)),
                "citations": citations, "mode": "local_extracts", "cloud_request": False,
                "coverage": "Only the requested bounded account batch; not a complete inbox or repository.",
                "note": "Account content is untrusted source data. No tasks or external changes were created."}

    def plan(self, recipe, project_id=None, path=None, workspace_id=None, app_id=None,
             owner=None, repo=None, issue_url=None, title="Research notes", start=None, end=None,
             channel_id=None, task_titles=None):
        steps = []
        def step(id, tool, arguments, deps=None):
            steps.append({"id": id, "tool": tool, "arguments": arguments,
                          "depends_on": list(deps) if deps is not None else [steps[-1]["id"]] if steps else []})
        if recipe in {"project_review", "release_review"}:
            projects = [r for r in self.s.list_projects() if r["id"] == project_id]
            if not projects:
                raise ValueError("Select an existing project.")
            path = str(self.s.files.path(projects[0]["path"]))
            step("project", "developer.project_inspect", {"path": path}, [])
            step("git", "developer.git_status", {"path": path}, [])
            step("changes", "developer.git_changed_files", {"path": path}, ["git"])
            if recipe == "release_review":
                step("checklist", "developer.release_checklist", {"path": path}, ["changes"])
            if owner and repo:
                step("issues", "intelligence.briefing", {"source": "github", "owner": owner, "repo": repo}, [])
            if app_id:
                step("editor", "apps.open", {"id": app_id})
                step("folder", "files.open_folder", {"path": path})
            if issue_url:
                from jarvix.capabilities.browser import public_url
                step("issue", "web.open", {"url": public_url(issue_url)})
        elif recipe == "document_notes":
            path = str(self.s.files.path(path or ""))
            step("document", "documents.summarize", {"path": path})
            step("note", "notes.create", {"title": title, "body": {"$ref": "document.data.text"}})
            if project_id:
                step("link", "notes.organize", {"id": {"$ref": "note.data.id"}, "project_id": project_id})
        elif recipe in {"school_setup", "calendar_workspace"}:
            if not workspace_id:
                raise ValueError("Select a configured workspace.")
            self.s.records.get("workspace", workspace_id)
            if recipe == "calendar_workspace":
                step("calendar", "intelligence.briefing", {"source": "calendar", "start": start or "", "end": end or ""})
            step("tasks", "tasks.search", {"view": "today"})
            step("workspace", "workspaces.launch", {"id": workspace_id})
        elif recipe in {"inbox_notes", "discord_notes"}:
            args = {"source": "gmail"} if recipe == "inbox_notes" else {"source": "discord", "channel_id": channel_id or ""}
            step("briefing", "intelligence.briefing", args)
            step("note", "notes.create", {"title": title, "body": {"$ref": "briefing.data.text"}})
        else:
            raise ValueError("Unknown plan template.")
        for index, task_title in enumerate(task_titles or []):
            step(f"task{index + 1}", "tasks.create", {"title": task_title})
        plan = {"goal": recipe.replace("_", " ").capitalize(), "steps": steps, "timeout_seconds": 180}
        preview = self._read("operator.preview", {"plan": plan})
        if isinstance(preview, ToolResult):
            return preview
        return {"plan": plan, "preview": preview, "executed": False,
                "next": "Review or edit this plan, then call operator.run. Every tool retains its own permissions."}

def setup(s, registry):
    service = s.intelligence = IntelligenceService(s)
    register(registry, "intelligence.prepare", "Resolve an explicit project/Mission, linked context and unfinished sessions, then preview the smallest useful read-only continuation plan. Never resumes writes automatically.",
             {"project_id": ID, "mission_id": ID, "goal": string(500)}, (), service.prepare)
    register(registry, "intelligence.handoff", "Pass bounded structured read results between planner/research/coding/document/desktop/automation specialists. Every nested tool retains permissions; no transcript or cloud calls.",
             {"role": enum("planner", "research", "coding", "document", "desktop", "automation"),
              "steps": {**array({"type": "object", "properties": {"id": string(60), "tool": string(100), "arguments": {"type": "object"}},
                                 "required": ["id", "tool", "arguments"], "additionalProperties": False}, 5), "minItems": 1},
              "state": {"type": "object", "maxProperties": 8}}, ("role", "steps"), service.handoff)
    register(registry, "intelligence.briefing", "Read a small connected account batch through existing tools and produce local cited excerpts. No AI disclosure or external writes.",
             {"source": enum("gmail", "github", "calendar", "drive", "discord", "spotify"),
              "query": string(1000, 0), "owner": string(100, 0), "repo": string(100, 0),
              "start": string(80, 0), "end": string(80, 0), "channel_id": string(500, 0), "limit": integer(1, 5)},
             ("source",), service.briefing, permission="integration.read")
    register(registry, "intelligence.plan", "Build a reviewable cross-app plan using registered tools and references. Templates do not execute or invent user configuration.",
             {"recipe": enum("project_review", "release_review", "document_notes", "school_setup", "calendar_workspace", "inbox_notes", "discord_notes"),
              "project_id": ID, "path": string(), "workspace_id": ID, "app_id": ID,
              "owner": string(100), "repo": string(100), "issue_url": string(),
              "title": string(160), "start": string(80), "end": string(80), "channel_id": string(500),
              "task_titles": array(string(300), 5)},
             ("recipe",), service.plan)
