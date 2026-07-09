from __future__ import annotations

from dataclasses import dataclass
import shutil


@dataclass(frozen=True)
class SystemSnapshot:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    battery_percent: float | None
    battery_plugged: bool | None
    storage_percent: float
    storage_used_gb: float
    storage_total_gb: float


class SystemInfoService:
    """Returns a compact local system snapshot."""

    def snapshot(self) -> SystemSnapshot:
        try:
            import psutil
        except ImportError as exc:
            raise RuntimeError(
                'psutil is required for system info. Run: python -m pip install -e ".[dev]"'
            ) from exc

        memory = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        storage = shutil.disk_usage("/")
        storage_percent = (storage.used / storage.total) * 100 if storage.total else 0
        return SystemSnapshot(
            cpu_percent=float(psutil.cpu_percent(interval=0.1)),
            ram_percent=float(memory.percent),
            ram_used_gb=round(memory.used / (1024**3), 2),
            ram_total_gb=round(memory.total / (1024**3), 2),
            battery_percent=None if battery is None else float(battery.percent),
            battery_plugged=None if battery is None else bool(battery.power_plugged),
            storage_percent=round(storage_percent, 2),
            storage_used_gb=round(storage.used / (1024**3), 2),
            storage_total_gb=round(storage.total / (1024**3), 2),
        )

