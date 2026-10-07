"""Local notification inbox; native delivery belongs to the desktop tray adapter."""
import threading
from datetime import datetime, timedelta, timezone
import copy
import json

from jarvix.capabilities.schema import BOOL, ID, array, enum, integer, register, schema, string
from jarvix.capabilities.context_graph import REFERENCE

QUICK_TOOLS = frozenset({"operator.session", "workflows.history", "missions.summary", "tasks.search",
                         "continuity.prepare", "intelligence.prepare", "skills.preview", "workspaces.list"})
ACTION = schema({"label": string(80), "tool": enum(*sorted(QUICK_TOOLS)), "arguments": {"type": "object"}},
                ("label", "tool", "arguments"))


class NotificationService:
    def __init__(self, services):
        self.s = services
        self._delivery_lock = threading.RLock()

    def _permitted(self, name):
        return name in self.s.enabled_tools() and not self.s.db.query(
            "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,))

    def _action(self, action):
        if (not isinstance(action, dict) or set(action) != {"label", "tool", "arguments"}
                or not isinstance(action["label"], str) or not 1 <= len(action["label"]) <= 80
                or action["tool"] not in QUICK_TOOLS or not isinstance(action["arguments"], dict)
                or len(json.dumps(action["arguments"], allow_nan=False)) > 4000):
            raise ValueError("Use a bounded registered inspection action.")
        spec = self.s.registry.get(action["tool"])
        if (spec.risk != "read" or spec.permission_level != 1 or not self._permitted(action["tool"])
                or self.s.registry.validate(action["tool"], action["arguments"])):
            raise PermissionError("This inspection action is unavailable or denied.")
        return copy.deepcopy(action)

    def create(self, title, body="", category="general", priority="normal", group_key="", related=None, actions=None):
        if (not isinstance(title, str) or not isinstance(body, str) or not title.strip()
                or len(title) > 160 or len(body) > 4000 or not isinstance(category, str)
                or not 1 <= len(category) <= 80 or priority not in {"low", "normal", "high"}
                or not isinstance(group_key, str) or len(group_key) > 160):
            raise ValueError("Invalid notification length.")
        related, actions = related or [], actions or []
        if not isinstance(related, list) or len(related) > 10 or not isinstance(actions, list) or len(actions) > 4:
            raise ValueError("Link at most ten related sources and four inspection actions.")
        refs = []
        for ref in related:
            if not isinstance(ref, dict) or set(ref) != {"kind", "reference"}:
                raise ValueError("Use typed context references.")
            if not self._permitted("context.graph"):
                raise PermissionError("Related context access is denied.")
            resolved = self.s.context_graph.resolve(**ref)
            refs.append({"kind": resolved["kind"], "reference": resolved["reference"]})
        actions = [self._action(action) for action in actions]
        return self.s.records.put("notification", {"title": title, "body": body,
            "category": category, "priority": priority, "read": False, "delivered": False,
            "group_key": group_key, "related": refs, "actions": actions})

    def _visible(self, row):
        row = copy.deepcopy(row)
        refs = []
        for ref in row.get("related", []):
            try:
                if not self._permitted("context.graph"):
                    raise PermissionError("Context is denied.")
                refs.append(self.s.context_graph.resolve(**ref))
            except PermissionError:
                row.update(title="Related source unavailable", body="", actions=[])
                refs.append({"available": False, "failure_class": "permission_denied"})
            except (ValueError, OSError):
                refs.append({**ref, "available": False, "failure_class": "source_unavailable"})
        row["related"] = refs
        allowed = []
        for action in row.get("actions", []):
            try:
                allowed.append(self._action(action))
            except (ValueError, PermissionError, KeyError):
                pass
        row["actions"] = allowed
        row["muted"] = row["category"] in self.s.settings.get("notifications.muted_categories", [])
        row["snoozed"] = self._snoozed(row)
        return row

    @staticmethod
    def _snoozed(row):
        try:
            until = datetime.fromisoformat(row.get("snoozed_until", ""))
            return until.tzinfo is not None and until > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False

    def list(self, unread_only=False, category="", limit=100):
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("Inspect a bounded notification batch.")
        if not self._permitted("notifications.list"):
            return []
        return [self._visible(row) for row in self.s.records.list("notification")
                if (not unread_only or not row["read"]) and (not category or row["category"] == category)][:limit]

    def groups(self, unread_only=False, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Inspect 1–100 notification groups.")
        if not self._permitted("notifications.groups"):
            raise PermissionError("Notification grouping is disabled or denied.")
        groups = {}
        for row in self.list(unread_only=unread_only, limit=2000):
            key = (row["category"], row.get("group_key") or row["category"])
            group = groups.setdefault(key, {"category": key[0], "group_key": key[1], "count": 0,
                                            "unread": 0, "items": []})
            group["count"] += 1
            group["unread"] += not row["read"]
            if len(group["items"]) < 5:
                group["items"].append(row)
        return list(groups.values())[:limit]

    def snooze(self, id, minutes=60):
        if type(minutes) is not int or not 0 <= minutes <= 10080:
            raise ValueError("Snooze for at most one week; zero clears snooze.")
        with self._delivery_lock:
            row = self.s.records.get("notification", id)
            row["snoozed_until"] = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat() if minutes else None
            row["delivered"] = False
            self.s.records.put("notification", row, id)
        return {"id": id, "snoozed_until": row["snoozed_until"]}

    def mute_category(self, category, muted=True):
        if not isinstance(category, str) or not 1 <= len(category) <= 80 or type(muted) is not bool:
            raise ValueError("Choose a notification category and a mute flag.")
        with self._delivery_lock:
            categories = set(self.s.settings.get("notifications.muted_categories", []))
            categories.add(category) if muted else categories.discard(category)
            if len(categories) > 100:
                raise ValueError("Mute at most 100 categories.")
            self.s.settings.set("notifications.muted_categories", sorted(categories))
        return {"muted_categories": sorted(categories)}

    def action_preview(self, id, index=0):
        if type(index) is not int or not 0 <= index <= 3:
            raise ValueError("Choose an existing inspection action.")
        if not self._permitted("notifications.list") or not self._permitted("notifications.action_preview"):
            raise PermissionError("Notification inspection is disabled or denied.")
        row = self.s.records.get("notification", id)
        visible = self._visible(row)
        if any(not ref.get("available", True) for ref in visible["related"]):
            raise PermissionError("A related source is unavailable; refresh this notification.")
        try:
            action = self._action(row.get("actions", [])[index])
        except IndexError:
            raise ValueError("This notification has no such inspection action.") from None
        return {**action, "actions_executed": 0, "requires_host_execution": True}

    def mark_read(self, id):
        row = self.s.records.get("notification", id)
        row["read"] = True
        self.s.records.put("notification", row, id)
        return {"read": True}

    def clear(self, read_only=True):
        rows = self.list(limit=2000)
        count = 0
        for row in rows:
            if not read_only or row["read"]:
                self.s.records.delete("notification", row["id"])
                count += 1
        return {"removed": count}

    def delivery(self):
        with self._delivery_lock:
            return self._delivery()

    def _delivery(self):
        if not self._permitted("notifications.list"):
            return []
        rows = [row for row in self.s.records.list("notification") if not row["delivered"] and not self._snoozed(row)]
        muted = self.s.settings.get("notifications.quiet", False) or self.s.settings.get("notifications.dnd", False)
        categories = self.s.settings.get("notifications.muted_categories", [])
        suppressed = [row for row in rows if row["category"] in categories]
        for row in suppressed:
            row["delivered"] = True
            self.s.records.put("notification", row, row["id"])
        rows = [row for row in rows if row["category"] not in categories]
        if not muted:
            rows.sort(key=lambda row: {"high": 0, "normal": 1, "low": 2}.get(row["priority"], 1))
            selected, seen = [], set()
            for row in rows:
                key = (row["category"], row.get("group_key")) if row.get("group_key") else (row["id"],)
                if key not in seen and len(selected) < 5:
                    seen.add(key)
                    selected.append(row)
            group_ids = {(row["category"], row.get("group_key")) for row in selected if row.get("group_key")}
            rows = [row for row in rows if row in selected or (row["category"], row.get("group_key")) in group_ids]
        for row in rows:
            row["delivered"] = True
            self.s.records.put("notification", row, row["id"])
        if muted:
            return []
        return [self._visible(row) for row in selected]

    def preferences(self, quiet=False, do_not_disturb=False):
        self.s.settings.set("notifications.quiet", quiet)
        self.s.settings.set("notifications.dnd", do_not_disturb)
        return {"quiet": quiet, "do_not_disturb": do_not_disturb}


def setup(s, registry):
    service = s.notifications = NotificationService(s)
    register(registry, "notifications.list", "Read your local notification inbox.",
             {"unread_only": BOOL, "category": string(80, 0), "limit": integer(1, 200)}, (), service.list)
    register(registry, "notifications.create", "Create a local desktop notification.",
             {"title": string(160), "body": string(4000, 0), "category": string(80),
              "priority": enum("low", "normal", "high"), "group_key": string(160, 0),
              "related": array(REFERENCE, 10), "actions": array(ACTION, 4)}, ("title",), service.create,
             level=2, permission="notifications.write")
    register(registry, "notifications.mark_read", "Mark a notification read.", {"id": ID}, ("id",), service.mark_read, 2)
    register(registry, "notifications.clear", "Remove local notification history.", {"read_only": BOOL}, (), service.clear, 3)
    register(registry, "notifications.preferences", "Set quiet mode and do-not-disturb.",
             {"quiet": BOOL, "do_not_disturb": BOOL}, (), service.preferences, 2)
    register(registry, "notifications.groups", "Inspect bounded notification groups by category and related work.",
             {"unread_only": BOOL, "limit": integer(1, 100)}, (), service.groups)
    register(registry, "notifications.snooze", "Persist a notification snooze; zero clears it.",
             {"id": ID, "minutes": integer(0, 10080)}, ("id",), service.snooze, 2)
    register(registry, "notifications.mute_category", "Mute a notification category while retaining its inbox history.",
             {"category": string(80), "muted": BOOL}, ("category",), service.mute_category, 2)
    register(registry, "notifications.action_preview", "Validate a saved inspection shortcut. Returns a preview and never executes its tool.",
             {"id": ID, "index": integer(0, 3)}, ("id",), service.action_preview)
