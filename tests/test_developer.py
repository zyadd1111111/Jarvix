import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from jarvix.runtime import operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "project"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    yield service
    service.close()


@pytest.fixture
def git_repo(services):
    executable = shutil.which("git")
    if not executable:
        pytest.skip("Git is unavailable")
    root = services.test_root
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments):
        return subprocess.run([executable, "-C", str(root), *arguments], check=True,
                              capture_output=True, text=True, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout

    git("-c", "init.templateDir=", "init")
    (root / "visible.txt").write_text("before\n")
    (root / ".env").write_text("token=secret-before\n")
    git("add", "--", "visible.txt", ".env")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    (root / "visible.txt").write_text("after\n")
    (root / ".env").write_text("token=secret-after\n")
    return root, git


def test_git_diff_filters_sensitive_files_and_rejects_directory_bypass(services, git_repo):
    root, _ = git_repo
    developer = services.developer
    result = developer.git_diff(str(root))
    assert "+after" in result["text"]
    assert "secret" not in result["text"] and result["excluded"] == 1
    assert developer.git_changed(str(root))["working"] == ["visible.txt"]
    for path in (".env", ".", str(root)):
        with pytest.raises(ValueError):
            developer.git_diff(str(root), file=path)


def test_git_diff_deleted_directory_cannot_expand_secret_file_request(services, git_repo):
    root, git = git_repo
    folder = root / "old"
    folder.mkdir()
    secret = folder / ".env"
    secret.write_text("private-deleted-value\n")
    git("add", "--", "old/.env")
    secret.unlink()
    folder.rmdir()
    result = services.developer.git_diff(str(root), file="old")
    assert result["text"] == "" and result["excluded"] == 1


def test_git_inspection_never_invokes_repository_helpers(services, git_repo):
    root, git = git_repo
    script = root / "helper.py"
    marker = root / "helper-executed.txt"
    script.write_text("from pathlib import Path\nPath(__file__).with_name('helper-executed.txt').write_text('unsafe')\n")
    command = f'"{sys.executable}" "{script}"'
    git("config", "filter.fixture.clean", command)
    git("config", "filter.fixture.required", "true")
    git("config", "diff.fixture.textconv", command)
    git("config", "diff.external", command)
    git("config", "core.fsmonitor", command)
    (root / ".gitattributes").write_text("*.txt filter=fixture diff=fixture\n")
    assert "visible.txt" in services.developer.git_status(str(root))["text"]
    assert "+after" in services.developer.git_diff(str(root), file="visible.txt")["text"]
    assert not marker.exists()


def test_project_initializers_never_replace_existing_files(services):
    developer, root = services.developer, services.test_root
    for language, filename in (("python", "main.py"), ("node", "package.json"), ("lua", "main.lua")):
        target = root / language
        result = developer.initialize(str(target), language)
        assert (target / filename).is_file() and result["dependencies_installed"] is False
        before = (target / filename).read_bytes()
        with pytest.raises(ValueError):
            developer.initialize(str(target), language)
        assert (target / filename).read_bytes() == before
        services.files.undo(result["operation_id"])
        assert not target.exists()


def test_command_denial_and_cancellation_prevent_process_creation(services, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
    arguments = {"argv": [sys.executable, "-c", "pass"], "cwd": str(services.test_root)}
    services.settings.set("control.enabled", True)
    denied = services.execute_tool("developer.command_start", arguments, approve=lambda _: False)
    assert not denied.ok and calls == []
    cancel = threading.Event()
    with operation(cancel=cancel):
        cancel.set()
        with pytest.raises(InterruptedError):
            services.developer.command_start(**arguments)
    assert calls == []


def test_owned_command_output_history_and_cancel(services):
    developer = services.developer
    secret = "fixture-sensitive-output"
    result = services.execute_tool("developer.command_start", {
        "argv": [sys.executable, "-u", "-c", f"import time; print('{secret}', flush=True); time.sleep(30)"],
        "cwd": str(services.test_root), "timeout": 15,
    }, approve=lambda _: True)
    assert result.ok
    session_id = result.data["session_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        output = developer.command_output(session_id)
        if secret in output["output"]:
            break
        time.sleep(0.02)
    assert secret in output["output"] and output["running"]
    assert developer.command_output(session_id, offset=output["offset"])["output"] == ""
    assert secret not in str(developer.command_history())
    assert developer.command_cancel(session_id)["stopped"]
    assert not developer.command_output(session_id)["running"]
    assert developer.command_output(session_id)["stop_reason"] == "cancelled"
    with pytest.raises(ValueError):
        developer.command_cancel("unowned-process")


def test_command_timeout_terminates_only_owned_process(services):
    developer = services.developer
    result = developer.command_start([sys.executable, "-c", "import time; time.sleep(30)"],
                                     str(services.test_root), timeout=1)
    session = developer._session(result["session_id"])
    session.watchdog.join(5)
    assert session.process.poll() is not None and session.reason == "timeout"
