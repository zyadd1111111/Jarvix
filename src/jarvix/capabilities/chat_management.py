"""Conversation organization, local exports, and explicit provider context metadata."""
import ast
import json
import operator

from jarvix.capabilities.schema import BOOL, ID, integer, register, string


class ConversationService:
    def __init__(self, services):
        self.s = services

    def search(self, query="", pinned_only=False, project_id=""):
        rows = self.s.list_conversations()
        result = []
        for row in rows:
            try:
                meta = self.s.records.get("conversation_meta", row["id"])
            except ValueError:
                meta = {}
            if pinned_only and not meta.get("pinned"):
                continue
            if project_id and meta.get("project_id") != project_id:
                continue
            if query.casefold() not in row["title"].casefold():
                messages = self.s.conversation_messages(row["id"])
                if not any(query.casefold() in item["content"].casefold() for item in messages):
                    continue
            result.append({**row, "pinned": meta.get("pinned", False), "folder": meta.get("folder", ""),
                           "project_id": meta.get("project_id", "")})
        return sorted(result[:100], key=lambda row: not row["pinned"])

    def configure(self, id, title=None, pinned=None, folder=None, project_id=None):
        if not self.s.db.query("SELECT id FROM conversations WHERE id=?", (id,)):
            raise ValueError("Conversation not found")
        if title is not None:
            if not title.strip():
                raise ValueError("Title is required")
            self.s.db.execute("UPDATE conversations SET title=? WHERE id=?", (title[:200], id))
        try:
            meta = self.s.records.get("conversation_meta", id)
        except ValueError:
            meta = {}
        for key, value in (("pinned", pinned), ("folder", folder), ("project_id", project_id)):
            if value is not None:
                meta[key] = value
        self.s.records.put("conversation_meta", meta, id)
        return {"updated": True}

    def branch(self, id, before_index):
        rows = self.s.conversation_messages(id)
        if not 0 <= before_index < len(rows) or rows[before_index]["role"] != "user":
            raise ValueError("Choose a user message to revise.")
        source = self.s.db.query("SELECT title FROM conversations WHERE id=?", (id,))[0]
        new_id = self.s.new_conversation((source["title"][:180] + " · revised")[:200])
        for row in rows[:before_index]:
            self.s.repository.append_message(new_id, row["role"], row["content"])
        return {"id": new_id, "draft": rows[before_index]["content"]}

    def delete(self, id):
        self.s.repository.delete("conversations", id)
        self.s.records.delete("conversation_meta", id)
        return {"deleted": True}

    def export(self, id, path):
        target = self.s.files.path(path, existing=False)
        rows = self.s.conversation_messages(id)
        if not rows:
            raise ValueError("Conversation has no messages")
        text = "\n\n".join("## " + row["role"].title() + "\n\n" + row["content"] for row in rows)
        with target.open("x", encoding="utf-8") as handle:
            handle.write(text)
        return {"path": str(target), "messages": len(rows)}

    def activity(self, query="", kind="", since="", until="", limit=100):
        # This searches sanitized summaries, never tool argument/result bodies.
        rows = self.s.activity(500)
        return [row for row in rows if query.casefold() in row["summary"].casefold()
                and (not kind or row["kind"] == kind) and (not since or row["created_at"] >= since)
                and (not until or row["created_at"] <= until)][:limit]


def calculate(expression):
    """Arithmetic only: no eval, imports, attribute access or resource-heavy powers."""
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                 ast.Div: operator.truediv, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
    parsed = ast.parse(expression, mode="eval")
    if len(list(ast.walk(parsed))) > 80:
        raise ValueError("Expression too complex")
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) < 1e15:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            value = operators[type(node.op)](visit(node.left), visit(node.right))
            if abs(value) > 1e30:
                raise ValueError("Result is too large")
            return value
        raise ValueError("Use basic arithmetic only")
    return {"result": visit(parsed.body)}


def setup(s, registry):
    service = s.conversations = ConversationService(s)
    register(registry, "conversations.search", "Search local conversation titles and content.",
             {"query": string(200, 0), "pinned_only": BOOL, "project_id": string(160, 0)}, (), service.search)
    register(registry, "conversations.configure", "Rename, pin or organize a conversation into a folder/project.",
             {"id": ID, "title": string(200), "pinned": BOOL, "folder": string(100, 0), "project_id": string(160, 0)}, ("id",), service.configure, 2)
    register(registry, "conversations.delete", "Permanently remove a conversation and its messages.", {"id": ID}, ("id",), service.delete, 3)
    register(registry, "conversations.export", "Export a conversation to a new Markdown file in an allowed root.",
             {"id": ID, "path": string()}, ("id", "path"), service.export, 2)
    register(registry, "activity.search", "Search sanitized activity by date and action type.",
             {"query": string(200, 0), "kind": string(80, 0), "since": string(40, 0), "until": string(40, 0), "limit": integer(1, 500)}, (), service.activity)
    register(registry, "calculate.evaluate", "Calculate bounded arithmetic locally without running code.",
             {"expression": string(500)}, ("expression",), calculate)

    def export_data(path):
        target = s.files.path(path, existing=False)
        payload = {"notes": s.list_notes(), "tasks": s.list_tasks(), "memories": s.list_memories(),
                   "projects": s.list_projects(), "conversations": [{**row, "messages": s.conversation_messages(row["id"])} for row in s.list_conversations()]}
        with target.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
        return {"exported": True, "path": str(target), "credentials_included": False}
    register(registry, "data.export", "Export local notes, tasks, memories, projects and conversations; excludes credentials.",
             {"path": string()}, ("path",), export_data, 3)
