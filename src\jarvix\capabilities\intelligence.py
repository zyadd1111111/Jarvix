"""Cross-app plan templates and account briefings composed through existing tools.

Templates are structured starting points for the planner, not phrase matching.
Briefings extract source evidence locally; they never invoke a cloud model.
"""
from __future__ import annotations

from jarvix.capabilities.evidence import terms
from jarvix.capabilities.schema import ID, array, enum, integer, register, string
from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled


class IntelligenceService:
    def __init__(self, services):
        self.s = services

    def _read(self, name, args):
        check_cancelled()
        result = self.s.execute_tool(name, args)
        if not result.ok:
            return result
        return result.data

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
        if recipe == "project_review":
            projects = [r for r in self.s.list_projects() if r["id"] == project_id]
            if not projects:
                raise ValueError("Select an existing project.")
            path = str(self.s.files.path(projects[0]["path"]))
            step("project", "developer.project_inspect", {"path": path}, [])
            step("git", "developer.git_status", {"path": path}, [])
            step("changes", "developer.git_changed_files", {"path": path}, ["git"])
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
        preview = self.s.operator.preview(plan)
        return {"plan": plan, "preview": preview, "executed": False,
                "next": "Review or edit this plan, then call operator.run. Every tool retains its own permissions."}

def setup(s, registry):
    service = s.intelligence = IntelligenceService(s)
    register(registry, "intelligence.briefing", "Read a small connected account batch through existing tools and produce local cited excerpts. No AI disclosure or external writes.",
             {"source": enum("gmail", "github", "calendar", "drive", "discord", "spotify"),
              "query": string(1000, 0), "owner": string(100, 0), "repo": string(100, 0),
              "start": string(80, 0), "end": string(80, 0), "channel_id": string(500, 0), "limit": integer(1, 5)},
             ("source",), service.briefing, permission="integration.read")
    register(registry, "intelligence.plan", "Build a reviewable cross-app plan using registered tools and references. Templates do not execute or invent user configuration.",
             {"recipe": enum("project_review", "document_notes", "school_setup", "calendar_workspace", "inbox_notes", "discord_notes"),
              "project_id": ID, "path": string(), "workspace_id": ID, "app_id": ID,
              "owner": string(100), "repo": string(100), "issue_url": string(),
              "title": string(160), "start": string(80), "end": string(80), "channel_id": string(500),
              "task_titles": array(string(300), 5)},
             ("recipe",), service.plan)
