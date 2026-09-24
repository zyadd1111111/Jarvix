from pathlib import Path
from types import SimpleNamespace
import sys
import threading

import pytest

from jarvix.domain import ToolResult, ToolSpec
from jarvix.tools import ToolRegistry, build_registry
from jarvix.tools.builtin import MAX_FILE_BYTES
from jarvix.voice import VoiceService, WINDOWS_SCRIPT


class FakeServices:
    def __init__(self, roots=()):
        self.roots = list(roots)
        self.notes = [{"id": "note-1", "title": "Jarvix", "body": "Local first", "private_extra": "not exported"}]
        self.memories = []
        self.tasks = []
        self.files = []
        self.launched = []
        # Match the facade's service boundaries while keeping these unit tests in memory.
        from jarvix.capabilities.browser import BrowserService
        self.repository = SimpleNamespace(audit=lambda *_: None)
        self.browser = BrowserService(self)
        self.productivity = SimpleNamespace(memories=SimpleNamespace(list=self.search_memories))

    def search_memories(self, query=""):
        return {"items": [row for row in self.memories if query.casefold() in row["content"].casefold()]}

    def file_roots(self):
        return self.roots

    def list_notes(self):
        return self.notes

    def save_note(self, title, body, note_id=None):
        self.notes.append({"id": "new-note", "title": title, "body": body})
        return "new-note"

    def list_memories(self):
        return self.memories

    def add_memory(self, content):
        self.memories.append({"id": "new-memory", "content": content})
        return "new-memory"

    def list_tasks(self):
        return self.tasks

    def add_task(self, title, due_at=None):
        self.tasks.append({"id": "new-task", "title": title, "due_at": due_at})
        return "new-task"

    def list_files(self, query=""):
        return [item for item in self.files if query.casefold() in item["path"].casefold()]

    def list_projects(self):
        return []

    def list_apps(self):
        return [{"id": "editor", "name": "Editor"}]

    def launch_app(self, app_id):
        if app_id != "editor":
            raise ValueError("Sensitive executable path")
        self.launched.append(app_id)
        return {"launched": "Editor", "pid": 100}

    def system_snapshot(self):
        return {"cpu_percent": 12, "memory_percent": 30}


@pytest.fixture
def services(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    return FakeServices([str(allowed)])


def test_registry_validates_before_execution():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}),
                      lambda args: (calls.append(args) or ToolResult(True, args)))
    for args in ({}, {"text": 1}, {"text": "hello", "extra": "bad"}, [], {"text": "x" * 50000}):
        assert not registry.execute("echo", args).ok
    assert calls == []
    assert registry.validate("echo", {"text": "hello"}) is None
    assert calls == []
    assert registry.execute("echo", {"text": "hello"}).data == {"text": "hello"}
    assert len(calls) == 1
    assert not registry.execute("not_registered", {}).ok
    with pytest.raises(ValueError, match="Unknown tool"):
        registry.get("not_registered")


def test_registry_safe_errors_and_immutable_specs():
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    def fail(args):
        raise RuntimeError("secret-token-123")
    registry.register(ToolSpec("test.fail", "Failure", schema), fail)
    schema["additionalProperties"] = True
    registry.get("test.fail").parameters["additionalProperties"] = True
    registry.specs()[0].parameters["additionalProperties"] = True
    assert not registry.execute("test.fail", {"injected": True}).ok
    result = registry.execute("test.fail", {})
    assert not result.ok and "secret" not in result.error
    assert result.sensitivity == "local"


def test_registry_rejects_duplicate_and_unbounded_output():
    registry = ToolRegistry()
    spec = ToolSpec("large", "Large", {"type": "object"})
    registry.register(spec, lambda args: ToolResult(True, "x" * 70000))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec, lambda args: ToolResult(True))
    assert not registry.execute("large", {}).ok


def test_all_tools_have_closed_structured_schemas(services):
    specs = build_registry(services).specs()
    assert len(specs) == 15
    for spec in specs:
        assert spec.parameters["type"] == "object"
        assert spec.parameters["additionalProperties"] is False
        assert spec.permission
    by_name = {spec.name: spec for spec in specs}
    assert by_name["notes.create"].risk == "write"
    assert by_name["apps.open"].risk == "external"
    assert by_name["web.open"].risk == "external"


def test_real_local_record_tools_keep_data_local(services):
    registry = build_registry(services)
    assert registry.execute("notes.create", {"title": "Design", "body": "Gemini provider"}).ok
    notes = registry.execute("notes.search", {"query": "GEMINI"})
    assert notes.data["items"][0]["title"] == "Design"
    assert notes.sensitivity == "local"
    assert "private_extra" not in registry.execute("notes.search", {}).data["items"][0]
    assert registry.execute("memory.remember", {"content": "Jarvix is my main project"}).ok
    assert registry.execute("memory.search", {"query": "main project"}).data["items"]
    assert registry.execute("tasks.create", {"title": "Ship foundation", "due_at": "2026-09-17T12:30:00-04:00"}).ok
    assert registry.execute("tasks.list", {}).data["items"][0]["title"] == "Ship foundation"


@pytest.mark.parametrize("due", ["tomorrow!!", "2026-09-17", "2026-09-17T12:00:00", "2026-99-99T12:00:00Z"])
def test_task_tool_rejects_ambiguous_or_invalid_due_times(services, due):
    assert not build_registry(services).execute("tasks.create", {"title": "Task", "due_at": due}).ok
    assert services.tasks == []


def test_file_boundary_and_content_limits(services, tmp_path):
    allowed = Path(services.roots[0])
    inside = allowed / "note.md"
    inside.write_text("Local text\n" * 100, encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("Private contents", encoding="utf-8")
    sibling = tmp_path / "allowed-evil"
    sibling.mkdir()
    sibling_file = sibling / "file.txt"
    sibling_file.write_text("outside prefix", encoding="utf-8")
    registry = build_registry(services)
    result = registry.execute("files.read_text", {"path": str(inside), "max_chars": 100})
    assert result.ok and result.sensitivity == "local"
    assert len(result.data["text"]) == 100 and result.data["truncated"]
    for invalid in (str(outside), str(sibling_file), "../private.txt", str(allowed / ".." / "private.txt")):
        assert not registry.execute("files.read_text", {"path": invalid}).ok
    services.files = [{"path": str(p)} for p in (inside, outside, sibling_file)]
    found = registry.execute("files.search", {})
    assert found.ok and len(found.data["items"]) == 1
    assert found.data["items"][0]["name"] == "note.md"


def test_file_symlink_cannot_escape_root(services, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("Must stay private", encoding="utf-8")
    link = Path(services.roots[0]) / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevated privileges.")
    services.files = [{"path": str(link)}]
    registry = build_registry(services)
    assert not registry.execute("files.read_text", {"path": str(link)}).ok
    assert registry.execute("files.search", {}).data["items"] == []


def test_retargeted_registered_root_cannot_expand_permission(services, tmp_path):
    registered = Path(services.roots[0])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("Keep local", encoding="utf-8")
    registered.rmdir()
    try:
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(outside), str(registered))
        else:
            registered.symlink_to(outside, target_is_directory=True)
    except OSError:
        registered.mkdir(exist_ok=True)
        pytest.skip("Directory links are not available.")
    try:
        candidate = registered / "private.txt"
        services.files = [{"path": str(candidate)}]
        registry = build_registry(services)
        assert not registry.execute("files.read_text", {"path": str(candidate)}).ok
        assert registry.execute("files.search", {}).data["items"] == []
    finally:
        if sys.platform == "win32":
            registered.rmdir()
        else:
            registered.unlink()
        registered.mkdir()


def test_files_require_registered_root(services):
    services.roots = []
    registry = build_registry(services)
    assert not registry.execute("files.search", {}).ok
    assert not registry.execute("files.read_text", {"path": "C:/example.txt"}).ok


@pytest.mark.parametrize("name,content", [
    ("large.txt", b"x" * (MAX_FILE_BYTES + 1)),
    ("binary.txt", b"\x00\x01\x02"),
    ("bad.txt", b"\xff\xfe"),
    ("program.exe", b"text"),
    (".env", b"API_KEY=secret"),
    (".env.local", b"API_KEY=secret"),
    ("credentials.json", b'{"token":"secret"}'),
], ids=["oversize", "binary", "encoding", "executable", "dotenv", "dotenv-variant", "credentials"])
def test_file_tool_rejects_oversize_binary_and_sensitive_files(services, name, content):
    target = Path(services.roots[0]) / name
    target.write_bytes(content)
    assert not build_registry(services).execute("files.read_text", {"path": str(target)}).ok


def test_browser_tools_are_explicit_and_never_claim_fetched_content(services, monkeypatch):
    opened = []
    monkeypatch.setattr("jarvix.capabilities.browser.webbrowser.open", lambda url, new=0: (opened.append(url) or True))
    registry = build_registry(services)
    result = registry.execute("web.search", {"query": "private project & q=oops"})
    assert result.ok and result.sensitivity == "public"
    assert "q=private%20project%20%26%20q%3Doops" in opened[0]
    assert result.data == {"opened": True}
    assert registry.execute("web.open", {"url": "https://github.com"}).ok
    assert len(opened) == 2


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/system32/cmd.exe", "javascript:alert(1)", "https://user:secret@example.com",
    "https://example.com\nignored", "http://localhost", "http://127.0.0.1", "http://127.1",
    "http://0x7f.1", "http://192.168.1.5", "https://example.com:99999", "https://example.com\\evil",
])
def test_browser_validation_rejects_unsafe_urls(services, monkeypatch, url):
    monkeypatch.setattr("jarvix.capabilities.browser.webbrowser.open", lambda *a, **k: pytest.fail("Browser must not open"))
    assert not build_registry(services).execute("web.open", {"url": url}).ok


def test_app_tool_accepts_only_registered_id_schema(services):
    registry = build_registry(services)
    assert not registry.execute("apps.open", {"id": "editor", "args": ["-execute"]}).ok
    assert not registry.execute("apps.open", {"id": "cmd.exe"}).ok
    assert registry.execute("apps.open", {"id": "editor"}).ok
    assert services.launched == ["editor"]


def test_read_outputs_bounded(services):
    services.notes = [{"id": str(i), "title": "Long", "body": "x" * 16000} for i in range(100)]
    result = build_registry(services).execute("notes.search", {})
    assert result.ok and len(result.data["items"]) == 30 and result.data["truncated"]


def test_voice_reports_no_microphone_and_unavailable_engine(monkeypatch):
    monkeypatch.setattr(VoiceService, "_discover_command", staticmethod(lambda: None))
    voice = VoiceService()
    assert not voice.available and not voice.input_available
    assert not voice.speak("Hello")
    voice.stop()
    voice.close()


def test_windows_speech_script_does_not_interpolate_user_text():
    assert "ReadToEnd" in WINDOWS_SCRIPT
    assert "$speaker.Speak($text)" in WINDOWS_SCRIPT
    assert "InputEncoding" in WINDOWS_SCRIPT


@pytest.mark.parametrize("rate", [175, 300, 9999, -10])
def test_voice_sends_text_as_stdin_and_terminates_owned_process(monkeypatch, rate):
    entered, stopped = threading.Event(), threading.Event()
    captured = {}
    payload = 'Hello; $(Remove-Item) "quoted" café'

    class FakeProcess:
        returncode = None
        def __init__(self, command, **kwargs):
            captured.update(command=command, kwargs=kwargs)
        def communicate(self, input):
            captured["input"] = input
            entered.set()
            stopped.wait(2)
            self.returncode = 0
        def poll(self):
            return self.returncode
        def terminate(self):
            captured["terminated"] = True
            stopped.set()

    monkeypatch.setattr(VoiceService, "_discover_command", staticmethod(lambda: ["speech", "constant-script"]))
    monkeypatch.setattr("jarvix.voice.subprocess.Popen", FakeProcess)
    voice = VoiceService()
    try:
        assert voice.speak(payload, rate=rate)
        assert entered.wait(2)
        assert voice.is_speaking
        assert captured["input"] == payload.encode("utf-8")
        assert payload not in captured["command"]
        assert captured["kwargs"]["shell"] is False
        bounded_rate = max(80, min(300, rate))
        if sys.platform == "win32":
            assert int(captured["kwargs"]["env"]["JARVIX_SPEECH_RATE"]) == round((bounded_rate - 175) / 20)
        else:
            assert captured["command"][-1] == str(bounded_rate)
        voice.stop()
        assert captured["terminated"] and not voice.is_speaking
    finally:
        stopped.set()
        voice.close()
