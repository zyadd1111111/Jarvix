import json
import subprocess

import pytest

from jarvix.capabilities.windows_uia import run_native_script


class Worker:
    def __init__(self, hang=False):
        self.hang = hang
        self.returncode = None
        self.killed = False
        self.inputs = []

    def communicate(self, input=None, timeout=None):
        self.inputs.append(input)
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout)
        self.returncode = 0
        return json.dumps({"ok": True, "data": {"verified": True}}).encode(), b""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1


def test_native_worker_confines_untrusted_text_to_stdin_json(monkeypatch):
    worker = Worker()
    commands = []
    monkeypatch.setattr("jarvix.capabilities.windows_uia.windows_only", lambda: None)
    monkeypatch.setattr("jarvix.capabilities.windows_uia.subprocess.Popen",
                        lambda command, **options: commands.append((command, options)) or worker)
    text = '$(Invoke-Expression "danger"); private text é'
    result = run_native_script("# trusted fixed source", {"text": text})
    assert result["verified"]
    assert text not in str(commands)
    assert json.loads(worker.inputs[0]) == {"text": text}
    assert commands[0][1]["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize("cause", ["cancel", "timeout"])
def test_native_worker_is_killed_and_reaped_on_stop(monkeypatch, cause):
    worker = Worker(hang=True)
    monkeypatch.setattr("jarvix.capabilities.windows_uia.windows_only", lambda: None)
    monkeypatch.setattr("jarvix.capabilities.windows_uia.subprocess.Popen", lambda *a, **k: worker)
    calls = [0]
    current_time = [0.0]

    def clock():
        current_time[0] += 0.2
        return current_time[0]

    def checkpoint():
        calls[0] += 1
        if cause == "cancel" and calls[0] >= 3:
            raise InterruptedError("cancelled")

    if cause == "timeout":
        monkeypatch.setattr("jarvix.capabilities.windows_uia.time.monotonic", clock)
    with pytest.raises(InterruptedError if cause == "cancel" else TimeoutError):
        run_native_script("# fixed", {}, checkpoint=checkpoint, timeout=0.1 if cause == "timeout" else 12)
    assert worker.killed
    assert worker.inputs[-1] is None  # Reaped through communicate after termination.

