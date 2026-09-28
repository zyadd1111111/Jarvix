"""Bounded, declarative workflow conditions; no Python or shell expressions."""
from __future__ import annotations

from datetime import datetime, time
from itertools import islice

import psutil

from jarvix.runtime import check_cancelled


def clock_time(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("Time must use HH:MM.")
    try:
        result = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Time must use HH:MM.") from exc
    return result


def weekdays(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 7 or any(
        type(day) is not int or not 0 <= day <= 6 for day in value
    ):
        raise ValueError("Choose weekdays from Monday=0 through Sunday=6.")
    return sorted(set(value))


def process_names():
    names = set()
    for process in islice(psutil.process_iter(["name"]), 2000):
        check_cancelled()
        try:
            names.add((process.info["name"] or "").casefold())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return names


def network_connected():
    stats = psutil.net_if_stats()
    addresses = psutil.net_if_addrs()
    return any(stat.isup and any(
        str(address.address) not in {"127.0.0.1", "::1"}
        and address.family.name in {"AF_INET", "AF_INET6"}
        for address in addresses.get(name, [])
    ) for name, stat in stats.items())


class ConditionEvaluator:
    FIELDS = {
        "app_running": {"name"}, "process": {"name", "running"}, "file_exists": {"path"},
        "time_range": {"start", "end"}, "weekday": {"days"}, "battery": {"charging", "below"},
        "network": {"connected"}, "task": {"id", "status"}, "workspace": {"id"},
        "safe": {"key", "op", "value"},
    }

    def __init__(self, services):
        self.s = services

    def validate(self, condition):
        if not isinstance(condition, dict) or condition.get("kind") not in self.FIELDS:
            raise ValueError("Choose a supported structured condition.")
        kind = condition["kind"]
        if set(condition) - self.FIELDS[kind] - {"kind"}:
            raise ValueError("Unknown condition field.")
        if kind in {"app_running", "process"}:
            name = condition.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(c in name for c in "\0\r\n/\\"):
                raise ValueError("Provide an exact process filename.")
            if kind == "process" and type(condition.get("running", True)) is not bool:
                raise ValueError("Running must be a boolean.")
        elif kind == "file_exists":
            if not isinstance(condition.get("path"), str):
                raise ValueError("Choose a path inside allowed roots.")
            self.s.files.path(condition["path"], existing=False)
        elif kind == "time_range":
            clock_time(condition.get("start"))
            clock_time(condition.get("end"))
        elif kind == "weekday":
            weekdays(condition.get("days"))
        elif kind == "battery":
            if "charging" not in condition and "below" not in condition:
                raise ValueError("Choose battery charging state or a threshold.")
            if "charging" in condition and type(condition["charging"]) is not bool:
                raise ValueError("Charging must be a boolean.")
            if "below" in condition and (type(condition["below"]) not in {int, float} or not 0 <= condition["below"] <= 100):
                raise ValueError("Battery threshold must be from 0 to 100.")
        elif kind == "network":
            if type(condition.get("connected")) is not bool:
                raise ValueError("Connected must be a boolean.")
        elif kind in {"task", "workspace"}:
            if not isinstance(condition.get("id"), str) or not 1 <= len(condition["id"]) <= 160:
                raise ValueError("Select a saved task or workspace.")
            if kind == "task" and condition.get("status") not in {"open", "done"}:
                raise ValueError("Task state must be open or done.")
        elif kind == "safe":
            if condition.get("key") not in {"cpu_percent", "memory_percent", "battery_percent"}:
                raise ValueError("Safe facts are CPU, memory, or battery percent.")
            if condition.get("op") not in {"lt", "le", "eq", "ge", "gt"}:
                raise ValueError("Choose a supported comparison.")
            if type(condition.get("value")) not in {int, float} or not 0 <= condition["value"] <= 100:
                raise ValueError("Comparison value must be from 0 to 100.")
        return dict(condition)

    def evaluate(self, condition, now=None):
        check_cancelled()
        c = self.validate(condition)
        kind = c["kind"]
        local = (now or datetime.now().astimezone()).astimezone()
        if kind in {"app_running", "process"}:
            return (c["name"].casefold() in process_names()) == c.get("running", True)
        if kind == "file_exists":
            return self.s.files.path(c["path"], existing=False).exists()
        if kind == "time_range":
            start, end, current = clock_time(c["start"]), clock_time(c["end"]), local.time()
            return start <= current < end if start <= end else current >= start or current < end
        if kind == "weekday":
            return local.weekday() in c["days"]
        if kind == "battery":
            battery = psutil.sensors_battery()
            return bool(battery and ("charging" not in c or battery.power_plugged == c["charging"])
                        and ("below" not in c or battery.percent < c["below"]))
        if kind == "network":
            return network_connected() == c["connected"]
        if kind == "task":
            return any(item["id"] == c["id"] and item["status"] == c["status"] for item in self.s.list_tasks())
        if kind == "workspace":
            return self.s.settings.get("workspace.active_id", "") == c["id"]
        if c["key"] == "cpu_percent":
            actual = psutil.cpu_percent()
        elif c["key"] == "memory_percent":
            actual = psutil.virtual_memory().percent
        else:
            battery = psutil.sensors_battery()
            if not battery:
                return False
            actual = battery.percent
        expected = c["value"]
        return {"lt": actual < expected, "le": actual <= expected, "eq": actual == expected,
                "ge": actual >= expected, "gt": actual > expected}[c["op"]]

