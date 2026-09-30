"""Explicit knowledge spaces extend document collections with local source references."""
from __future__ import annotations

from pathlib import Path

from jarvix.capabilities.documents import SUPPORTED
from jarvix.capabilities.schema import BOOL, ID, enum, integer, register, string
from jarvix.capabilities.search import digest, drive_identity, source_key
from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled
from jarvix.tools.builtin import TEXT_NAMES

SOURCE_KINDS = ("folder", "document", "note", "conversation", "repository", "drive")


class KnowledgeSpaceService:
    def __init__(self, services):
        self.s = services

    def create(self, name, project_id=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Name requires 1–200 characters.")
        if project_id and not any(item["id"] == project_id for item in self.s.list_projects()):
            raise ValueError("Select an existing project.")
        record_id = self.s.records.put("knowledge.space", {"name": name.strip(), "project_id": project_id,
                                                          "sources": [], "last_refresh": None})
        return {"id": record_id, "name": name.strip(), "sources": 0, "local_only": True}

    def list(self, project_id=None):
        rows = [{"id": item["id"], "name": item["name"], "project_id": item.get("project_id"),
                 "sources": len(item["sources"]), "last_refresh": item.get("last_refresh"), "updated_at": item["updated_at"]}
                for item in self.s.records.list("knowledge.space")
                if project_id is None or item.get("project_id") == project_id]
        return {"items": rows[:100], "truncated": len(rows) > 100}

    def get(self, id):
        space = self.s.records.get("knowledge.space", id)
        # Only source references are returned; no file reads or account calls.
        return {**space, "cloud_upload": False, "automatic_refresh": False}

    def _drive(self, file_id):
        result = self.s.execute_tool("google_drive.metadata", {"file_id": file_id})
        check_cancelled()
        if not result.ok:
            return result
        value = result.data
        if not isinstance(value, dict) or not value.get("id") or not value.get("name"):
            raise ValueError("Drive returned invalid file metadata.")
        selected = {key: value[key] for key in ("id", "name", "mimeType", "modifiedTime", "description", "webViewLink")
                    if key in value and isinstance(value[key], str)}
        if any(len(text) > 8000 for text in selected.values()):
            raise ValueError("Drive metadata exceeds the local limit.")
        text = "\n".join(selected.get(key, "") for key in ("name", "description", "mimeType", "modifiedTime"))
        source = self.s.search.text_source("drive", file_id, selected["name"], text,
                                         {"metadata_only": True, "url": selected.get("webViewLink", "")})
        account_key = drive_identity(self.s)
        source["signature"] = digest([source["signature"], account_key, selected])
        self.s.records.put("knowledge.drive", {"metadata": selected, "signature": source["signature"],
                                              "account_key": account_key}, file_id)
        return source

    def add_source(self, id, kind, reference):
        if kind not in SOURCE_KINDS or not isinstance(reference, str) or not reference.strip() or len(reference) > 4096:
            raise ValueError("Choose a supported source reference.")
        space = self.s.records.get("knowledge.space", id)
        if len(space["sources"]) >= 100:
            raise ValueError("A space supports at most 100 explicit sources.")
        if kind in {"folder", "repository", "document"}:
            target = self.s.files.path(reference)
            if kind == "document":
                self.s.search.file_source(target)
            elif not target.is_dir():
                raise ValueError("Select an allowed folder.")
            if kind == "repository" and not (target / ".git").exists():
                raise ValueError("The selected folder is not a Git repository.")
            reference, title = str(target), target.name
        elif kind in {"note", "conversation"}:
            method = self.s.list_notes if kind == "note" else self.s.list_conversations
            found = next((item for item in method() if item["id"] == reference), None)
            if found is None:
                raise ValueError("Source no longer exists.")
            title = found["title"]
        else:
            source = self._drive(reference)
            if isinstance(source, ToolResult):
                return source
            title = source["title"]
        source_id = source_key(kind, reference)
        existing = next((item for item in space["sources"] if item["id"] == source_id), None)
        if existing:
            return {"id": id, "source_id": source_id, "already_added": True}
        space["sources"].append({"id": source_id, "kind": kind, "reference": reference, "title": title[:300]})
        space["last_refresh"] = None
        self.s.records.put("knowledge.space", space, id)
        return {"id": id, "source_id": source_id, "sources": len(space["sources"]), "content_loaded": False,
                "metadata_only": kind == "drive", "cloud_upload": False}

    def _matching_keys(self, sources):
        keys = set()
        manifests = self.s.records.list("search.source")
        for source in sources:
            check_cancelled()
            if source["kind"] in {"folder", "repository", "document"}:
                try:
                    target = self.s.files.path(source["reference"])
                    for manifest in manifests:
                        if manifest["kind"] not in {"document", "source"}:
                            continue
                        path = Path(manifest["source_id"])
                        if path == target or (target.is_dir() and path.is_relative_to(target)):
                            keys.add(manifest["id"])
                except (ValueError, OSError):
                    continue
            else:
                keys.add(source_key(source["kind"], source["reference"]))
        return keys

    def source_keys(self, id):
        return self._matching_keys(self.s.records.get("knowledge.space", id)["sources"])

    def refresh(self, id, force=False):
        space = self.s.records.get("knowledge.space", id)
        snapshot = digest(space["sources"])
        kinds = {source["kind"] for source in space["sources"]} & {"note", "conversation"}
        local = {source_key(item["kind"], item["source_id"]): item for item in self.s.search.local_sources(kinds)}
        sources, errors, limited = {}, [], False
        for source in space["sources"]:
            check_cancelled()
            try:
                if source["kind"] in {"folder", "repository", "document"}:
                    target = self.s.files.path(source["reference"])
                    candidates = [target] if source["kind"] == "document" else self.s.files.walk(target)
                    for path in candidates:
                        check_cancelled()
                        if not path.is_file() or (path.suffix.casefold() not in SUPPORTED and path.name.casefold() not in TEXT_NAMES):
                            continue
                        item = self.s.search.file_source(path)
                        key = source_key(item["kind"], item["source_id"])
                        if key not in sources and len(sources) >= 300:
                            limited = True
                            break
                        sources[key] = item
                elif source["kind"] == "drive":
                    item = self._drive(source["reference"])
                    if isinstance(item, ToolResult):
                        raise ValueError("Drive metadata could not be refreshed; inspect connection/permissions.")
                    sources[source_key(item["kind"], item["source_id"])] = item
                else:
                    key = source_key(source["kind"], source["reference"])
                    if key not in local:
                        raise ValueError("The selected local source no longer exists.")
                    sources[key] = local[key]
            except InterruptedError:
                raise
            except (ValueError, OSError):
                errors.append({"source_id": source["id"], "reason": "Source unavailable, denied or outside allowed access."})
        result = self.s.search.rebuild_sources(list(sources.values()), force)
        current = self.s.records.get("knowledge.space", id)
        if digest(current["sources"]) != snapshot:
            raise ValueError("Space sources changed during refresh; refresh the new selection.")
        current["last_refresh"] = {"at": result["updated_at"], "indexed": result["indexed"], "failed": result["failed"] + len(errors),
                                   "limited": limited or result["limited"], "mode": result["mode"]}
        self.s.records.put("knowledge.space", current, id)
        return {**result, "failed": result["failed"] + len(errors), "space_id": id, "errors": errors, "limited": current["last_refresh"]["limited"],
                "metadata_only_sources": sum(source["kind"] == "drive" for source in space["sources"])}

    def search(self, id, query, limit=20):
        space = self.get(id)
        result = self.s.search.query(query, limit=limit, space_id=id)
        return {**result, "space_id": id, "needs_refresh": not bool(space.get("last_refresh"))}

    def question(self, id, question, limit=5):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
            raise ValueError("Choose 1–10 evidence excerpts.")
        found = self.search(id, question, limit)
        return {"space_id": id, "question": question, "answer": found["items"][0]["text"] if found["items"] else None,
                "evidence": found["items"], "mode": found["mode"], "warning": found["warning"],
                "needs_refresh": found["needs_refresh"], "stale_sources": found["stale_sources"],
                "method": "Top verbatim cited evidence; no synthesized answer or automatic cloud upload.", "cloud_request": False}

    def summarize(self, id, limit=8):
        space = self.get(id)
        overview = self.s.search.excerpts(self.source_keys(id), limit)
        text = "\n\n".join(f"[{index}] {item['title']}\n{item['text']}" for index, item in enumerate(overview["items"], 1))
        return {**overview, "space_id": id, "text": text, "needs_refresh": not bool(space.get("last_refresh"))}

    def remove_source(self, id, source_id):
        space = self.s.records.get("knowledge.space", id)
        removed = [item for item in space["sources"] if item["id"] == source_id]
        if not removed:
            raise ValueError("Space source not found.")
        space["sources"] = [item for item in space["sources"] if item["id"] != source_id]
        space["last_refresh"] = None
        self.s.records.put("knowledge.space", space, id)
        for source in removed:
            if source["kind"] == "drive" and not any(source["reference"] == candidate["reference"] and candidate["kind"] == "drive"
                    for item in self.s.records.list("knowledge.space") for candidate in item["sources"]):
                self.s.records.delete("knowledge.drive", source["reference"])
                self.s.search.remove("drive", source["reference"])
        return {"removed": True, "source_deleted": False, "sources": len(space["sources"])}

    def delete(self, id):
        space = self.s.records.get("knowledge.space", id)
        for source in list(space["sources"]):
            self.remove_source(id, source["id"])
        self.s.records.delete("knowledge.space", id)
        return {"deleted": True, "sources_deleted": False}

    def import_collection(self, collection_id):
        collection = self.s.records.get("knowledge.collection", collection_id)
        # Validate every reference before creating anything; legacy collections remain intact.
        for path in collection["paths"]:
            self.s.search.file_source(path)
        created = self.create(collection["name"], collection.get("project_id"))
        for path in collection["paths"]:
            self.add_source(created["id"], "document", path)
        return {**created, "sources": len(collection["paths"]), "original_collection_id": collection_id}


def setup(s, registry):
    service = s.knowledge_spaces = KnowledgeSpaceService(s)
    register(registry, "knowledge_spaces.create", "Create an empty local Knowledge Space; sources are referenced explicitly and never uploaded automatically.",
             {"name": string(200), "project_id": ID}, ["name"], service.create, 2, "knowledge.write")
    register(registry, "knowledge_spaces.list", "List Knowledge Spaces and their explicit refresh state without reading sources.",
             {"project_id": ID}, [], service.list)
    register(registry, "knowledge_spaces.get", "Inspect a Knowledge Space's selected references and refresh status.", {"id": ID}, ["id"], service.get)
    register(registry, "knowledge_spaces.add_source", "Add an allowed folder/document/repository, saved note/conversation or selected Drive metadata reference. Drive access retains account permissions; no upload.",
             {"id": ID, "kind": enum(*SOURCE_KINDS), "reference": string(4096)}, ["id", "kind", "reference"], service.add_source, 2, "knowledge.write")
    register(registry, "knowledge_spaces.remove_source", "Remove a space reference without deleting its original source.",
             {"id": ID, "source_id": ID}, ["id", "source_id"], service.remove_source, 2, "knowledge.write")
    register(registry, "knowledge_spaces.refresh", "Explicitly refresh space sources using the local index; unchanged sources are skipped. Roots and account access are rechecked.",
             {"id": ID, "force": BOOL}, ["id"], service.refresh, 2, "knowledge.write")
    register(registry, "knowledge_spaces.search", "Search current allowed Knowledge Space evidence with local hybrid or transparently labeled keyword ranking.",
             {"id": ID, "query": string(1000), "limit": integer(1, 30)}, ["id", "query"], service.search)
    register(registry, "knowledge_spaces.question", "Answer a Knowledge Space question using a verbatim cited evidence excerpt; no cloud synthesis.",
             {"id": ID, "question": string(1000), "limit": integer(1, 10)}, ["id", "question"], service.question)
    register(registry, "knowledge_spaces.summarize", "Produce a bounded local overview of cited verbatim excerpts from current space sources.",
             {"id": ID, "limit": integer(1, 10)}, ["id"], service.summarize)
    register(registry, "knowledge_spaces.delete", "Delete the Knowledge Space and its references, preserving original documents and notes.",
             {"id": ID}, ["id"], service.delete, 3, "knowledge.write")
    register(registry, "knowledge_spaces.import_collection", "Copy an existing project document collection into a Knowledge Space; preserve the original collection.",
             {"collection_id": ID}, ["collection_id"], service.import_collection, 2, "knowledge.write")
