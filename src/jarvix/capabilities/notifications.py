"""Local notification inbox; native delivery belongs to the desktop tray adapter."""
import threading

from jarvix.capabilities.schema import BOOL, ID, enum, integer, register, string


class NotificationService:
    def __init__(self, services):
        self.s = services
        self._delivery_lock = threading.Lock()

    def create(self, title, body="", category="general", priority="normal"):
        if not title.strip() or len(title) > 160 or len(body) > 4000:
            raise ValueError("Invalid notification length.")
        return self.s.records.put("notification", {"title": title, "body": body,
            "category": category, "priority": priority, "read": False, "delivered": False})

    def list(self, unread_only=False, category="", limit=100):
        return [row for row in self.s.records.list("notification")
                if (not unread_only or not row["read"]) and (not category or row["category"] == category)][:limit]

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
        rows = [row for row in self.list(limit=2000) if not row["delivered"]]
        muted = self.s.settings.get("notifications.quiet", False) or self.s.settings.get("notifications.dnd", False)
        if not muted:
            rows.sort(key=lambda row: {"high": 0, "normal": 1, "low": 2}.get(row["priority"], 1))
            rows = rows[:5]
        for row in rows:
            row["delivered"] = True
            self.s.records.put("notification", row, row["id"])
        if muted:
            return []
        return rows

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
              "priority": enum("low", "normal", "high")}, ("title",), service.create,
             level=2, permission="notifications.write")
    register(registry, "notifications.mark_read", "Mark a notification read.", {"id": ID}, ("id",), service.mark_read, 2)
    register(registry, "notifications.clear", "Remove local notification history.", {"read_only": BOOL}, (), service.clear, 3)
    register(registry, "notifications.preferences", "Set quiet mode and do-not-disturb.",
             {"quiet": BOOL, "do_not_disturb": BOOL}, (), service.preferences, 2)
