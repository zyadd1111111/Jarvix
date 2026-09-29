"""Cited local document intelligence and opt-in project knowledge references."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter

from jarvix.capabilities import document_parsers as parsers
from jarvix.capabilities.schema import ID, array, enum, integer, register, string
from jarvix.runtime import check_cancelled
from jarvix.tools.builtin import TEXT_EXTENSIONS, TEXT_NAMES

SUPPORTED = TEXT_EXTENSIONS | {".pdf", ".docx", ".pptx", ".xlsx"}
STOP_WORDS = frozenset("a an and are as at be been but by can did do does for from had has have how i if in into is it its may me my not of on or our should so than that the their them then there these they this to was we were what when where which who why will with would you your".split())


def _terms(text):
    return [word for word in re.findall(r"[^\W_]{2,}", text.casefold()) if word not in STOP_WORDS]


def _page(items, cursor=0, limit=20, budget=42000):
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0 or cursor > len(items):
        raise ValueError("Invalid continuation cursor.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
        raise ValueError("Page size must be between 1 and 30.")
    selected, size = [], 0
    for item in items[cursor:cursor + limit]:
        item_size = len(json.dumps(item, ensure_ascii=False))
        if size + item_size > budget:
            break
        size += item_size
        selected.append(item)
    if not selected and cursor < len(items):
        raise ValueError("A source fragment exceeds the response limit.")
    end = cursor + len(selected)
    return {"items": selected, "total": len(items), "cursor": cursor,
            "next_cursor": end if end < len(items) else None, "truncated": end < len(items)}


class DocumentService:
    def __init__(self, services):
        self.s = services

    def _read(self, path, expected_revision=None):
        check_cancelled()
        target = self.s.files.path(path)
        if not target.is_file() or (target.suffix.casefold() not in SUPPORTED and target.name.casefold() not in TEXT_NAMES):
            raise ValueError("Select a supported document or text source file.")
        before = target.stat()
        if before.st_size > parsers.MAX_FILE_BYTES:
            raise ValueError("Document exceeds the 24 MiB file limit.")
        with target.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not os.path.samestat(before, opened):
                raise ValueError("File changed before reading; retry.")
            chunks, size = [], 0
            while chunk := handle.read(256 * 1024):
                check_cancelled()
                size += len(chunk)
                if size > parsers.MAX_FILE_BYTES:
                    raise ValueError("Document exceeds the 24 MiB file limit.")
                chunks.append(chunk)
            after = os.fstat(handle.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ValueError("File changed while reading; retry.")
        # Re-check configured roots, links and current identity before and after parsing.
        if not os.path.samestat(after, self.s.files.path(target).stat()):
            raise ValueError("File was replaced while reading; retry.")
        data = b"".join(chunks)
        revision = hashlib.sha256(data).hexdigest()
        if expected_revision and revision != expected_revision:
            raise ValueError("Document changed; restart extraction with cursor zero.")
        document = parsers.parse(data, target.suffix.casefold())
        current = self.s.files.path(target).stat()
        if (not os.path.samestat(after, current) or after.st_size != current.st_size
                or after.st_mtime_ns != current.st_mtime_ns):
            raise ValueError("File changed during extraction; retry.")
        cited = []
        for number, item in enumerate(document.segments):
            check_cancelled()
            cited.append({**item, "citation": {"id": f"{revision[:12]}:{number}", "path": str(target),
                                                "revision": revision, **item["location"]}})
        return {"path": str(target), "format": target.suffix.casefold().lstrip("."), "revision": revision,
                "characters": document.characters, "segments": cited, "warnings": document.warnings,
                "local_only": True}

    @staticmethod
    def _metadata(document):
        return {key: value for key, value in document.items() if key != "segments"}

    def extract(self, path, cursor=0, limit=20, kind="all", expected_revision=None):
        document = self._read(path, expected_revision)
        if kind not in {"all", "text", "heading", "table_cell"}:
            raise ValueError("Unsupported document fragment kind.")
        fragments = [item for item in document["segments"] if kind == "all" or item["kind"] == kind]
        return {**self._metadata(document), **_page(fragments, cursor, limit)}

    def sections(self, path, cursor=0, limit=20, expected_revision=None):
        document = self._read(path, expected_revision)
        sections = [item for item in document["segments"] if item["kind"] == "heading"]
        return {**self._metadata(document), **_page(sections, cursor, limit),
                "method": "Explicit Word, PowerPoint and Markdown headings only; no inferred headings."}

    def tables(self, path, cursor=0, limit=20, expected_revision=None):
        return self.extract(path, cursor, limit, "table_cell", expected_revision)

    @staticmethod
    def _matches(document, query):
        needle = query.strip().casefold()
        if not needle or len(needle) > 1000:
            raise ValueError("Enter a search query between 1 and 1,000 characters.")
        terms = set(_terms(needle))
        matches = []
        for item in document["segments"]:
            check_cancelled()
            text = item["text"].casefold()
            overlap = terms.intersection(_terms(text))
            if needle in text or overlap:
                score = (2 if needle in text else 0) + len(overlap) / max(1, len(terms))
                matches.append({**item, "score": round(score, 4), "matched_terms": sorted(overlap)})
        matches.sort(key=lambda item: -item["score"])
        return matches

    def search(self, path, query, cursor=0, limit=20, expected_revision=None):
        document = self._read(path, expected_revision)
        return {**self._metadata(document), **_page(self._matches(document, query), cursor, limit),
                "method": "Local literal phrase and keyword matching."}

    def summarize(self, path, limit=6):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
            raise ValueError("Choose between 1 and 10 summary excerpts.")
        document = self._read(path)
        frequencies = Counter(_terms("\n".join(item["text"] for item in document["segments"])))
        ranked = []
        seen = set()
        for position, item in enumerate(document["segments"]):
            check_cancelled()
            terms = set(_terms(item["text"]))
            normalized = item["text"].strip()
            if normalized in seen or not terms:
                continue
            seen.add(normalized)
            score = sum(frequencies[word] for word in terms) / max(1, len(terms))
            ranked.append((score, position, item))
        selected = sorted(sorted(ranked, key=lambda row: -row[0])[:limit], key=lambda row: row[1])
        # Keep complete cited fragments in a note-sized representation; never cut
        # a citation or silently pass a partial sentence to the next plan step.
        text = f"Local verbatim excerpts\nSource: {document['path']}\nRevision: {document['revision']}\n"
        excerpts = []
        for item in _page([row[2] for row in selected], limit=limit, budget=32000)["items"]:
            location = ", ".join(f"{key}: {value}" for key, value in item["location"].items() if value != "")
            block = f"\n[{len(excerpts) + 1}] {location}\n{item['text']}\n"
            if len(text) + len(block) > 16000:
                break
            excerpts.append(item)
            text += block
        return {**self._metadata(document), "method": "Local extractive summary; verbatim excerpts, not an AI-generated synopsis.",
                "text": text, "excerpts": excerpts, "total_segments": len(document["segments"]),
                "selection_only": len(excerpts) < len(document["segments"])}

    def question(self, path, question, limit=5):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
            raise ValueError("Choose between 1 and 10 evidence excerpts.")
        document = self._read(path)
        matches = self._matches(document, question)
        evidence = _page(matches, limit=limit)["items"]
        return {**self._metadata(document), "answer": evidence[0]["text"] if evidence else None,
                "evidence": evidence, "matches": len(matches), "truncated": len(matches) > len(evidence),
                "method": "Extractive keyword evidence; the answer is the top verbatim excerpt, not a synthesized claim.",
                "recovery": "Use documents.search with the same question and cursor for additional evidence." if len(matches) > len(evidence) else None}

    def compare(self, paths):
        if not 2 <= len(paths) <= 5 or len(set(paths)) != len(paths):
            raise ValueError("Select 2–5 distinct document paths.")
        documents = [self._read(path) for path in paths]
        term_sets = [set(_terms("\n".join(item["text"] for item in doc["segments"]))) for doc in documents]
        common = set.intersection(*term_sets)
        rows = []
        for index, document in enumerate(documents):
            check_cancelled()
            unique = sorted(term_sets[index] - set.union(*(terms for i, terms in enumerate(term_sets) if i != index)))
            evidence = []
            for item in document["segments"]:
                if set(_terms(item["text"])) & set(unique):
                    evidence.append(item)
                if len(evidence) == 2:
                    break
            rows.append({**self._metadata(document), "unique_terms": _page(unique, limit=30, budget=2200)["items"],
                         "unique_terms_total": len(unique), "evidence": evidence})
        # Citation paths can be long. Keep all document identities but reduce
        # evidence explicitly rather than failing the registry's result bound.
        for row in reversed(rows):
            while row["evidence"] and len(json.dumps(rows, ensure_ascii=False)) > 54000:
                row["evidence"].pop()
                row["evidence_truncated"] = True
        return {"documents": rows, "common_terms": _page(sorted(common), limit=30, budget=4000)["items"], "common_terms_total": len(common),
                "method": "Local vocabulary comparison; terms and cited evidence do not establish semantic agreement.",
                "local_only": True}

    def _paths(self, paths):
        if not 1 <= len(paths) <= 50:
            raise ValueError("A knowledge collection requires 1–50 document paths.")
        result = []
        for path in paths:
            check_cancelled()
            target = self.s.files.path(path)
            if not target.is_file() or (target.suffix.casefold() not in SUPPORTED and target.name.casefold() not in TEXT_NAMES):
                raise ValueError("Collections can reference only supported documents.")
            if str(target) not in result:
                result.append(str(target))
        return result

    def create_collection(self, name, paths, project_id=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Collection name requires 1–200 characters.")
        if project_id and not any(project["id"] == project_id for project in self.s.list_projects()):
            raise ValueError("Project does not exist.")
        paths = self._paths(paths)
        record_id = self.s.records.put("knowledge.collection", {"name": name.strip(), "paths": paths,
                                                                "project_id": project_id})
        return {"id": record_id, "name": name.strip(), "documents": len(set(paths)), "references_only": True}

    def update_collection(self, id, paths):
        record = self.s.records.get("knowledge.collection", id)
        record["paths"] = self._paths(paths)
        self.s.records.put("knowledge.collection", record, id)
        return {"id": id, "documents": len(record["paths"]), "references_only": True}

    def list_collections(self, project_id=None, cursor=0, limit=20):
        records = [{"id": item["id"], "name": item["name"], "project_id": item.get("project_id"),
                    "documents": len(item["paths"]), "updated_at": item["updated_at"]}
                   for item in self.s.records.list("knowledge.collection")
                   if project_id is None or item.get("project_id") == project_id]
        return _page(records, cursor, limit)

    def search_collection(self, id, query, document_cursor=0, cursor=0, limit=20, expected_revision=None):
        # One file per request: callers explicitly page files and evidence without
        # constructing enormous multi-file results or retaining revoked-root text.
        record = self.s.records.get("knowledge.collection", id)
        paths = record["paths"]
        if not isinstance(document_cursor, int) or isinstance(document_cursor, bool) or not 0 <= document_cursor < len(paths):
            raise ValueError("Invalid collection document cursor.")
        result = self.search(paths[document_cursor], query, cursor, limit, expected_revision)
        next_document = document_cursor + 1
        return {**result, "collection_id": id, "document_cursor": document_cursor, "document_count": len(paths),
                "next_document_cursor": next_document if result["next_cursor"] is None and next_document < len(paths) else None}


def setup(s, registry):
    s.documents = service = DocumentService(s)
    path = string(4096)
    paging = {"cursor": integer(0, parsers.MAX_SEGMENTS), "limit": integer(1, 30),
              "expected_revision": string(64, 64)}
    for name, description, handler in (
        ("extract", "Read local PDF, Office or text/source fragments with citations. Follow next_cursor using expected_revision; no cloud upload.", service.extract),
        ("sections", "List explicit Word, slide and Markdown headings with source citations and continuation.", service.sections),
        ("tables", "Extract literal Word/slide tables, spreadsheet and CSV cells with coordinates; formulas are not executed.", service.tables),
    ):
        props = {"path": path, **paging}
        if name == "extract":
            props["kind"] = enum("all", "text", "heading", "table_cell")
        register(registry, f"documents.{name}", description, props, ["path"], handler)
    register(registry, "documents.search", "Search every extracted fragment locally; return ranked, paged source citations.",
             {"path": path, "query": string(1000), **paging}, ["path", "query"], service.search)
    register(registry, "documents.summarize", "Select cited verbatim excerpts as a local extractive summary. Does not send content to AI.",
             {"path": path, "limit": integer(1, 10)}, ["path"], service.summarize)
    register(registry, "documents.question", "Answer with local keyword-matched verbatim evidence and citations; no synthesized or cloud answer.",
             {"path": path, "question": string(1000), "limit": integer(1, 10)}, ["path", "question"], service.question)
    register(registry, "documents.compare", "Compare vocabulary of 2–5 local documents and cite differences. No semantic or cloud inference.",
             {"paths": {**array(path, 5), "minItems": 2, "uniqueItems": True}}, ["paths"], service.compare)
    register(registry, "knowledge.create", "Create a local project knowledge collection referencing explicitly selected documents. No content is copied.",
             {"name": string(200), "paths": {**array(path, 50), "minItems": 1}, "project_id": ID},
             ["name", "paths"], service.create_collection, 2, "knowledge.write")
    register(registry, "knowledge.update", "Replace a knowledge collection's approved document references.",
             {"id": ID, "paths": {**array(path, 50), "minItems": 1}}, ["id", "paths"], service.update_collection, 2, "knowledge.write")
    register(registry, "knowledge.list", "List saved local knowledge collections by project without loading document contents.",
             {"project_id": ID, "cursor": integer(0, 2000), "limit": integer(1, 30)}, [], service.list_collections)
    register(registry, "knowledge.search", "Search one collection document at a time. Follow next_cursor then next_document_cursor; roots are rechecked on every read.",
             {"id": ID, "query": string(1000), "document_cursor": integer(0, 49), **paging},
             ["id", "query"], service.search_collection)
