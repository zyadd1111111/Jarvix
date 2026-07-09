from __future__ import annotations

import sys
from types import SimpleNamespace

from tools.system_info import SystemInfoService


class FakePsutil:
    @staticmethod
    def virtual_memory():
        return SimpleNamespace(percent=50.0, used=4 * 1024**3, total=8 * 1024**3)

    @staticmethod
    def sensors_battery():
        return SimpleNamespace(percent=88, power_plugged=True)

    @staticmethod
    def cpu_percent(interval=0.1):
        return 12.5


def test_system_snapshot_shape(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    snapshot = SystemInfoService().snapshot()

    assert snapshot.cpu_percent == 12.5
    assert snapshot.ram_percent == 50.0
    assert snapshot.ram_used_gb == 4.0
    assert snapshot.ram_total_gb == 8.0
    assert snapshot.battery_percent == 88.0
    assert snapshot.battery_plugged is True
    assert snapshot.storage_total_gb > 0

