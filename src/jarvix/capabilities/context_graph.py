"""Explicit local reference links; metadata is revalidated, never fetched or uploaded."""
from __future__ import annotations

import re
import threading
from urllib.parse import urlsplit

from jarvix.capabilities.browser import public_url
from jarvix.capabilities.schema import ID, enum, integer, register, schema, string
from jarvix.runtime import check_cancelled
from jarvix.services import required_text

TABLES = {"project": "projects", "task": "tasks", "note": "notes", "conversation": "conversations", "app": "apps"}
RECORDS = {"workspace": "workspace", "operator_session": "operator_session",
           "knowledge_space": "knowledge.space", "mission": "mission", "skill": "skill", "routine": "workflow"}
SOURCE_TOOLS = {"project": ("projects.list",), "task": ("tasks.list", "tasks.search"),
                "note": ("notes.search",), "conversation": ("conversations.search",),
                "workspace": ("workspaces.list",), "operator_session": ("operator.sessions", "operator.session"),
                "knowledge_space": ("knowledge_spaces.list", "knowledge_spaces.get"),
                "mission": ("missions.list", "missions.get"), "file": ("files.inspect",),
                "document": ("files.inspect",), "repository": ("developer.project_inspect",),
                "skill": ("skills.list", "skills.get"), "routine": ("routines.list", "workflows.preview"),
                "app": ("apps.list",)}
KINDS = (*TABLES, *RECORDS, "file", "document", "repository", "github_repository", "github_issue", "calendar_event")
REFERENCE = schema({"kind": enum(*KINDS), "reference": string(4096)}, ("kind", "reference"))
MISSION_LINKS = {"task_ids": "task", "note_ids": "note", "files": "file",
                 "knowledge_space_ids": "knowledge_space", "operator_session_ids": "operator_session",
                 "workspace_ids": "workspace", "conversation_ids": "conversation", "skill_ids": "skill"}
RELATION_METADATA = schema({"source": string(160), "type": enum("explicit"),
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "scope": enum("local")})


def relationship_metadata(source="user", type="explicit"):
    return {"source": source, "type": type, "confidence": 1, "scope": "local"}


def mission_references(mission):
    seen = set()
    if mission.get("project_id"):
        yield {"kind": "project", "reference": mission["project_id"]}
    for field, kind in MISSION_LINKS.items():
        for value in mission.get(field, []):
            seen.add((kind, value))
            yield {"kind": kind, "reference": value}
    for milestone in mission.get("milestones", []):
        for value in milestone.get("task_ids", []):
            if ("task", value) not in seen:
                seen.add(("task", value))
                yield {"kind": "task", "reference": value}


class ContextGraphService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()

    def resolve(self, kind, reference):
        """Return small live metadata; no source contents or external account calls."""
        if kind not in KINDS:
            raise ValueError("Unsupported context node type.")
        reference = required_text(reference, "Reference", 4096)
        for name in SOURCE_TOOLS.get(kind, ()):
            if name not in self.s.enabled_tools() or self.s.db.query(
                    "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,)):
                raise PermissionError("This context source is disabled or denied.")
        item = {"kind": kind, "reference": reference, "available": True}
        if kind in TABLES:
            label = "name" if kind in {"project", "app"} else "title"
            fields = f"id,{label}" + (",path" if kind == "project" else ",status" if kind == "task" else "")
            rows = self.s.db.query(f"SELECT {fields} FROM {TABLES[kind]} WHERE id=?", (reference,))
            if not rows:
                raise ValueError("Context source no longer exists.")
            item["label"] = rows[0][label]
            if kind == "project":
                path = self.s.files.path(rows[0]["path"])
                if not path.is_dir():
                    raise ValueError("Project folder is unavailable.")
                item["path"] = str(path)
            elif kind == "task":
                item["status"] = rows[0]["status"]
            elif kind == "app":
                item["registered"] = True
        elif kind in RECORDS:
            record = self.s.records.get(RECORDS[kind], reference)
            item["label"] = record.get("name", record.get("goal", kind))[:500]
            if kind in {"mission", "operator_session"}:
                item["status"] = record["status"]
            elif kind == "skill":
                item.update(enabled=record.get("enabled", False), version=record.get("version", 1))
            elif kind == "routine" and record.get("kind") != "routine":
                raise ValueError("This workflow is not a manual routine.")
            if kind == "workspace":
                for folder in record.get("folders", []):
                    if not self.s.files.path(folder).is_dir():
                        raise ValueError("Workspace folder is unavailable.")
            elif kind == "knowledge_space":
                item["source_count"] = len(record.get("sources", []))
                # Keep the space accessible even if an individual source was revoked.
                item["unavailable_sources"] = 0
                for source in record.get("sources", []):
                    try:
                        if source["kind"] == "folder":
                            if not self.s.files.path(source["reference"]).is_dir():
                                raise ValueError("Knowledge folder is unavailable.")
                        elif source["kind"] == "drive":
                            from jarvix.capabilities.search import drive_identity
                            cached = self.s.records.get("knowledge.drive", source["reference"])
                            if cached.get("account_key") != drive_identity(self.s):
                                raise ValueError("Knowledge account access changed.")
                        else:
                            self.resolve(source["kind"], source["reference"])
                    except (ValueError, OSError):
                        item["unavailable_sources"] += 1
        elif kind in {"file", "document", "repository"}:
            path = self.s.files.path(reference)
            if kind in {"file", "document"} and not path.is_file():
                raise ValueError("Choose an allowed file.")
            if kind == "repository" and (not path.is_dir() or not (path / ".git").exists()):
                raise ValueError("Choose an allowed Git repository.")
            item.update(reference=str(path), label=path.name)
        else:
            url = public_url(reference)
            parsed = urlsplit(url)
            if kind.startswith("github_"):
                suffix = r"/issues/[1-9][0-9]*" if kind == "github_issue" else ""
                if (parsed.scheme != "https" or parsed.hostname != "github.com"
                        or not re.fullmatch(r"/[^/]+/[^/]+" + suffix + r"/?", parsed.path)
                        or parsed.query or parsed.fragment):
                    raise ValueError("Use the exact public GitHub repository or issue URL.")
            item.update(reference=url, label=url, external=True, verified=False)
        return item

    def inspect_reference(self, kind, reference):
        try:
            return self.resolve(kind, reference)
        except (ValueError, OSError):
            return {"kind": kind, "reference": reference, "available": False,
                    "reason": "Source unavailable or outside current allowed access."}

    def link(self, source, target, relation="related", metadata=None):
        source_node = self.resolve(**source)
        target_node = self.resolve(**target)
        source = {key: source_node[key] for key in ("kind", "reference")}
        target = {key: target_node[key] for key in ("kind", "reference")}
        relation = required_text(relation, "Relation", 80)
        if source == target:
            raise ValueError("Link two different references.")
        from jsonschema import Draft202012Validator
        if metadata is not None and not Draft202012Validator(RELATION_METADATA).is_valid(metadata):
            raise ValueError("Relationship metadata must describe an explicit local relationship.")
        value = {"source": source, "target": target, "relation": relation,
                 "metadata": {**relationship_metadata(), **(metadata or {})}}
        with self._lock:
            edges = self.s.records.list("context.link")
            for edge in edges:
                if all(edge[key] == value[key] for key in ("source", "target", "relation")):
                    if edge.get("metadata", relationship_metadata()) != value["metadata"]:
                        self.s.records.put("context.link", value, edge["id"])
                        return {"id": edge["id"], "updated": True, "local_only": True}
                    return {"id": edge["id"], "already_linked": True, "local_only": True}
            if len(edges) >= 1000:
                raise ValueError("Remove an existing link before adding more than 1,000 context links.")
            id = self.s.records.put("context.link", value)
        return {"id": id, "local_only": True}

    def unlink(self, id):
        with self._lock:
            self.s.records.get("context.link", id)
            self.s.records.delete("context.link", id)
        return {"removed": True}

    def graph(self, kind=None, reference=None, depth=1, limit=100):
        if (not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 3
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100):
            raise ValueError("Invalid graph bounds.")
        if (kind is None) != (reference is None):
            raise ValueError("Provide both the starting kind and reference.")
        edges = [{**edge, "metadata": edge.get("metadata", relationship_metadata())}
                 for edge in self.s.records.list("context.link")]
        for mission in self.s.records.list("mission"):
            if mission.get("status") in {"archived", "cancelled"}:
                continue
            for target in mission_references(mission):
                edges.append({"id": f"mission:{mission['id']}:{target['kind']}:{target['reference']}",
                              "source": {"kind": "mission", "reference": mission["id"]}, "target": target,
                              "relation": "contains", "derived": True,
                              "metadata": relationship_metadata("missions.save", "derived")})
        for skill in self.s.records.list("skill"):
            targets = [{"kind": "routine", "reference": skill["routine_id"]}]
            source = skill.get("source", {})
            if source.get("kind") in {"operator_session", "skill"} and source.get("id"):
                targets.append({"kind": source["kind"], "reference": source["id"]})
            for target in targets:
                edges.append({"id": f"skill:{skill['id']}:{target['kind']}:{target['reference']}",
                              "source": {"kind": "skill", "reference": skill["id"]}, "target": target,
                              "relation": "uses" if target["kind"] == "routine" else "learned_from", "derived": True,
                              "metadata": relationship_metadata("skills.save", "derived")})
        chosen, nodes = [], {}
        frontier = None
        if kind is not None:
            if kind not in KINDS:
                raise ValueError("Unsupported context node type.")
            start = self.inspect_reference(kind, required_text(reference, "Reference", 4096))
            key = (start["kind"], start["reference"])
            frontier = {key}
            nodes[key] = start
        seen_edges = set()
        truncated = False
        for _ in range(depth if frontier is not None else 1):
            following = set()
            for edge in edges:
                check_cancelled()
                pair = [(edge[side]["kind"], edge[side]["reference"]) for side in ("source", "target")]
                if edge["id"] in seen_edges or (frontier is not None and not frontier.intersection(pair)):
                    continue
                if len(chosen) == limit:
                    truncated = True
                    continue
                seen_edges.add(edge["id"])
                chosen.append(edge)
                for node_kind, node_reference in pair:
                    key = (node_kind, node_reference)
                    if key not in nodes:
                        nodes[key] = self.inspect_reference(node_kind, node_reference)
                        following.add(key)
            if frontier is not None:
                frontier = following
                if not frontier:
                    break
        return {"nodes": list(nodes.values()), "edges": chosen, "truncated": truncated,
                "local_only": True, "automatic_cloud_sharing": False}


def setup(s, registry):
    s.context_graph = service = ContextGraphService(s)
    register(registry, "context.link", "Save an explicit local relationship between existing references; external links are never fetched.",
             {"source": REFERENCE, "target": REFERENCE, "relation": string(80), "metadata": RELATION_METADATA}, ("source", "target"), service.link, 2, "context.write")
    register(registry, "context.unlink", "Remove an explicit context relationship after fresh confirmation; preserves its source records.",
             {"id": ID}, ("id",), service.unlink, 3, "context.write")
    register(registry, "context.graph", "Inspect bounded linked metadata and current source availability, without reading files or fetching external references.",
             {"kind": enum(*KINDS), "reference": string(4096), "depth": integer(1, 3), "limit": integer(1, 100)}, (), service.graph)
