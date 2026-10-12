from datetime import datetime


def conversation_group(record, today=None):
    if record.get("pinned"):
        return "Pinned"
    today = today or datetime.now().date()
    try:
        date = datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00")).astimezone().date()
    except (KeyError, TypeError, ValueError):
        return "Older"
    days = (today - date).days
    return "Today" if days <= 0 else "Yesterday" if days == 1 else "Previous 7 days" if days <= 7 else "Older"
