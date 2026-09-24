"""First-party tools: local records, registered files/apps and explicit web actions."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jarvix.domain import ToolResult, ToolSpec
from jarvix.tools.registry import ToolRegistry

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".scss", ".js",
    ".jsx", ".ts", ".tsx", ".py", ".rs", ".go", ".java", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".sql", ".sh", ".ps1", ".bat", ".log",
    ".lua", ".luau",
})
TEXT_NAMES = frozenset({"readme", "license", "makefile", "dockerfile", ".gitignore"})
SENSITIVE_PARTS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube"})
MAX_FILE_BYTES = 256 * 1024


def _string(maximum: int, *, minimum: int = 1) -> dict:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


def _bounded_records(records: list[dict], query: str, fields: tuple[str, ...], limit: int = 30) -> dict:
    """Keep record results useful without copying unrelated columns or huge bodies."""
    needle = query.casefold().strip()
    matches: list[dict] = []
    truncated = False
    for item in records:
        if needle and not any(needle in str(item.get(key, "")).casefold() for key in fields):
            continue
        if len(matches) >= limit:
            truncated = True
            break
        record = {key: item[key] for key in fields if key in item}
        for key, value in record.items():
            if isinstance(value, str) and len(value) > 1200:
                record[key] = value[:1200] + "\n[truncated]"
        matches.append(record)
    return {"items": matches, "truncated": truncated}


def _is_sensitive(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    return bool(parts & SENSITIVE_PARTS) or name == ".env" or name.startswith(".env.") or name in {
        "credentials", "credentials.json", "secrets.json", "secrets.toml", "id_rsa", "id_ed25519",
    }


def _approved_path(value: str, roots: list[str]) -> Path | None:
    """Resolve symlinks before containment checks, including the configured roots."""
    from jarvix.capabilities.files import _linked
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        if any(_linked(part) for part in (candidate, *candidate.parents)):
            return None
        resolved = candidate.resolve(strict=True)
        if _is_sensitive(candidate) or _is_sensitive(resolved) or not resolved.is_file():
            return None
        for value_root in roots:
            configured = Path(value_root).expanduser()
            if not configured.is_absolute():
                continue
            root = configured.resolve(strict=True)
            # Services stores canonical absolute roots at registration. A root
            # replaced by a symlink/junction must not silently extend permission.
            if root != configured:
                continue
            if root.is_dir() and resolved.is_relative_to(root):
                return resolved
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def _valid_web_url(value: str) -> bool:
    """Browser navigation only. Reject credentials, control characters and local IPs."""
    try:
        if any(ord(char) < 33 for char in value) or "\\" in value:
            return False
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None or parsed.port == 0:
            return False
        host = parsed.hostname.casefold().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            ascii_host = host.encode("idna").decode("ascii")
            labels = ascii_host.split(".")
            return (len(ascii_host) <= 253 and len(labels) >= 2
                    and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels)
                    and bool(re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}", labels[-1])))
    except (ValueError, UnicodeError):
        return False


def build_registry(services: Any) -> ToolRegistry:
    registry = ToolRegistry()

    def register(name: str, description: str, parameters: dict, handler: Any,
                 permission: str = "local.read", risk: str = "read") -> None:
        level = 1 if risk == "read" else (3 if name == "memory.remember" else 2)
        registry.register(ToolSpec(name, description, parameters, permission, risk, level), handler)

    query_schema = _schema({"query": _string(200, minimum=0)})
    register("notes.search", "Search the user's local notes by text. Results stay local until sharing is approved.",
             query_schema, lambda a: ToolResult(True, _bounded_records(services.list_notes(), a.get("query", ""), ("id", "title", "body", "updated_at"))), "notes.read")
    register("notes.create", "Create an explicit local note.",
             _schema({"title": _string(160), "body": _string(16000, minimum=0)}, ["title", "body"]),
             lambda a: ToolResult(True, {"id": services.save_note(a["title"], a["body"])}), "notes.write", "write")
    register("memory.search", "Search facts the user explicitly chose to remember.", query_schema,
             lambda a: ToolResult(True, services.productivity.memories.list(query=a.get("query", ""))), "memory.read")
    register("memory.remember", "Save a fact only when the user asks Jarvix to remember it.",
             _schema({"content": _string(4000)}, ["content"]),
             lambda a: ToolResult(True, {"id": services.add_memory(a["content"])}), "memory.write", "write")
    register("tasks.list", "List the user's local tasks and reminder due times.", _schema(),
             lambda a: ToolResult(True, _bounded_records(services.list_tasks(), "", ("id", "title", "status", "due_at", "created_at"))), "tasks.read")
    def create_task(args: dict) -> ToolResult:
        due = args.get("due_at")
        if due is not None:
            try:
                parsed = datetime.fromisoformat(due.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
            except ValueError:
                return ToolResult(False, error="Use an ISO 8601 due time including a timezone, such as 2026-09-16T18:00:00-04:00.")
        return ToolResult(True, {"id": services.add_task(args["title"], due_at=due)})

    register("tasks.create", "Create a local task. Optional due_at is an ISO 8601 date/time with timezone; reminders run while Jarvix is open.",
             _schema({"title": _string(300), "due_at": {"type": "string", "minLength": 10, "maxLength": 40}}, ["title"]),
             create_task, "tasks.write", "write")

    def search_files(args: dict) -> ToolResult:
        roots = services.file_roots()
        if not roots:
            return ToolResult(False, error="Add a permitted folder in Files before searching local files.")
        matches = []
        truncated = False
        for item in services.list_files(args.get("query", "")):
            value = item.get("path", "")
            resolved = _approved_path(value, roots)
            if resolved is None:
                continue
            if len(matches) >= 40:
                truncated = True
                break
            matches.append({"path": str(resolved), "name": resolved.name, "size": resolved.stat().st_size})
        return ToolResult(True, {"items": matches, "truncated": truncated})

    def read_text(args: dict) -> ToolResult:
        roots = services.file_roots()
        if not roots:
            return ToolResult(False, error="Add a permitted folder in Files before reading local files.")
        path = _approved_path(args["path"], roots)
        if path is None:
            return ToolResult(False, error="File is unavailable, protected, or outside permitted folders.")
        if path.suffix.casefold() not in TEXT_EXTENSIONS and path.name.casefold() not in TEXT_NAMES:
            return ToolResult(False, error="This tool reads supported plain-text files only.")
        # Always cap bytes read, even if a file changes after the stat check.
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return ToolResult(False, error="File exceeds the 256 KiB reading limit.")
        if b"\x00" in raw:
            return ToolResult(False, error="File does not appear to be UTF-8 plain text.")
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ToolResult(False, error="Only UTF-8 plain-text files are supported.")
        maximum = args.get("max_chars", 12000)
        return ToolResult(True, {"path": str(path), "text": content[:maximum], "truncated": len(content) > maximum})

    register("files.search", "Search filenames only within folders the user registered in Files.", query_schema, search_files, "files.read")
    register("files.read_text", "Read a UTF-8 text file inside a permitted folder, up to 256 KiB. File contents require separate approval before cloud disclosure.",
             _schema({"path": _string(4096), "max_chars": {"type": "integer", "minimum": 100, "maximum": 16000}}, ["path"]), read_text, "files.read")
    register("projects.list", "List projects explicitly registered by the user.", _schema(),
             lambda a: ToolResult(True, _bounded_records(services.list_projects(), "", ("id", "name", "path", "description", "updated_at"))), "projects.read")
    register("apps.list", "List applications registered by the user and their stable IDs.", _schema(),
             lambda a: ToolResult(True, _bounded_records(services.list_apps(), "", ("id", "name", "path"))), "apps.read")
    register("apps.open", "Launch a registered application by its stable ID. Configured launch arguments require fresh confirmation.",
             _schema({"id": _string(120)}, ["id"]),
             lambda a: ToolResult(True, services.launch_app(a["id"])), "apps.launch", "external")

    def open_web(args: dict) -> ToolResult:
        if not _valid_web_url(args["url"]):
            return ToolResult(False, error="Use a public HTTP or HTTPS URL without credentials or control characters.")
        opened = services.browser.open_url(args["url"])["opened"]
        # Do not echo URL/query: browser actions may contain private user data.
        return ToolResult(bool(opened), {"opened": bool(opened)}, None if opened else "No browser could open the link.", "public")

    def search_web(args: dict) -> ToolResult:
        opened = services.browser.search(args["query"])["opened"]
        return ToolResult(bool(opened), {"opened": bool(opened)}, sensitivity="public")

    register("web.open", "Open a public HTTP(S) link in the default browser. Does not fetch or read page content.",
             _schema({"url": _string(4096)}, ["url"]), open_web, "web.open", "external")
    register("web.search", "Open a DuckDuckGo search in the default browser. Does not return web results or current facts.",
             _schema({"query": _string(500)}, ["query"]), search_web, "web.search", "external")
    register("system.status", "Read current system CPU, memory usage and high-memory processes.", _schema(),
             lambda a: ToolResult(True, services.system_snapshot()), "system.read")

    def processes(args: dict) -> ToolResult:
        import psutil
        rows = []
        for process in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = process.info
                if info["memory_info"] is not None:
                    rows.append({"pid": info["pid"], "name": info["name"] or "unknown", "memory_bytes": info["memory_info"].rss})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        rows.sort(key=lambda row: row["memory_bytes"], reverse=True)
        return ToolResult(True, {"items": rows[:args.get("limit", 12)], "sorted_by": "resident_memory_bytes"})

    register("system.processes", "Show running processes using the most RAM. Does not expose command-line arguments.",
             _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}), processes, "system.read")
    return registry
