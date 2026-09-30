"""Exercise composed tools through the real permission and result boundaries."""
from threading import Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from jarvix.domain import ToolResult
from jarvix.providers._transport import Transport
from jarvix.runtime import cancellable_lock, operation
from jarvix.services import Services


def test_extension_form_privacy_and_optional_download_access():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the offline browser extension check")
    subprocess.run([node, str(Path(__file__).with_name("browser_extension.cjs"))], check=True, timeout=10,
                   capture_output=True, text=True)


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    s.add_file_root(str(tmp_path))
    s.settings.set("control.enabled", True)
    s.settings.set("browser.control_enabled", True)
    yield s
    s.close()


def test_document_plan_creates_cited_note_and_respects_nested_denial(services, tmp_path):
    path = tmp_path / "lesson.md"
    path.write_text("# Lesson\nGravity attracts objects.\n", encoding="utf-8")
    args = {"recipe": "document_notes", "path": str(path), "title": "Physics", "task_titles": ["Review gravity"]}
    draft = services.execute_tool("intelligence.plan", args)
    assert draft.ok, draft.error
    assert not services.list_notes() and not draft.data["executed"]
    plan = draft.data["plan"]
    services.permissions.set_grant("notes.create", "deny")
    denied = services.operator.run(plan, approve=lambda _: True)
    assert not denied["ok"] and not services.list_notes()
    services.permissions.set_grant("notes.create", None)
    done = services.operator.run(plan, approve=lambda _: True)
    assert done["ok"], done
    note = services.list_notes()[0]
    assert "Gravity" in note["body"] and str(path) in note["body"]
    assert services.list_tasks()[0]["title"] == "Review gravity"


def test_project_and_workspace_templates_validate_registered_tools(services, tmp_path):
    project = services.add_project("Project", str(tmp_path))
    workspace = services.workspaces.save("School")
    for args in ({"recipe": "project_review", "project_id": project},
                 {"recipe": "school_setup", "workspace_id": workspace["id"]}):
        draft = services.execute_tool("intelligence.plan", args)
        assert draft.ok, draft.error
        assert draft.data["preview"] and not draft.data["executed"]


def test_account_briefing_cites_reads_and_never_bypasses_denied_tool(services, monkeypatch):
    invoke = Mock(side_effect=[ToolResult(True, {"messages": [{"id": "m1"}]}), ToolResult(True, {
        "headers": [{"name": "Subject", "value": "Homework"}], "text": "Read chapter two."})])
    monkeypatch.setattr(services.integrations, "invoke", invoke)
    result = services.execute_tool("intelligence.briefing", {"source": "gmail"})
    assert result.ok and result.data["citations"][0]["id"] == "m1"
    assert "Read chapter two" in result.data["text"] and not result.data["cloud_request"]
    assert [call.args[1] for call in invoke.call_args_list] == ["search", "read"]
    invoke.reset_mock()
    services.permissions.set_grant("gmail.search", "deny")
    assert not services.execute_tool("intelligence.briefing", {"source": "gmail"}).ok
    invoke.assert_not_called()
    assert not services.list_tasks() and not services.list_notes()


def test_direct_memory_ui_path_also_prevents_duplicate_insert(services):
    services.add_memory("Jarvix is my main project.")
    with pytest.raises(ValueError, match="matching memory"):
        services.add_memory("Jarvix is my main project")
    result = services.execute_tool("memory.remember", {"content": "Jarvix is my main project"}, approve=lambda _: True)
    assert not result.ok and result.data["requires_review"]
    assert len(services.list_memories()) == 1


def test_workflow_undo_reconfirms_and_refuses_changed_definition(services):
    created = services.execute_tool("workflows.save", {"name": "Morning", "steps": [
        {"kind": "action", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    assert created.ok
    receipt = created.data["undo_id"]
    assert services.undo.preview(receipt)["available"]
    requested = []
    denied = services.execute_tool("actions.undo", {"id": receipt},
        approve=lambda request: requested.append(request.tool_name) or False)
    assert not denied.ok and requested == ["workflows.delete"]
    services.workflows.toggle(created.data["id"], True)
    assert not services.undo.preview(receipt)["available"]
    assert not services.execute_tool("actions.undo", {"id": receipt}, approve=lambda _: True).ok


@pytest.mark.parametrize("variables", [None, {"topic": "OAuth"}])
def test_workflow_undo_restores_exact_previous_approved_config(services, variables):
    args = {"name": "Morning", "steps": [{"kind": "action", "tool": "tasks.list", "arguments": {}}]}
    if variables is not None:
        args["variables"] = variables
    original = services.execute_tool("workflows.save", args, approve=lambda _: True)
    before = services.workflows.get(original.data["id"])
    changed = {**args, "id": original.data["id"], "name": "Evening"}
    if variables is not None:
        changed["variables"] = {"topic": "Updated"}
    edited = services.execute_tool("workflows.save", changed,
                                   approve=lambda _: True)
    requested = []
    result = services.execute_tool("actions.undo", {"id": edited.data["undo_id"]},
        approve=lambda request: requested.append(request.tool_name) or True)
    assert result.ok, result.error
    assert requested == ["workflows.save"]
    restored = services.workflows.get(original.data["id"])
    assert restored["name"] == "Morning"
    assert restored.get("variables", {}) == (variables or {})
    assert restored["approval_fingerprint"] == before["approval_fingerprint"]


def test_rollback_reports_partial_results_and_preview_checks_links(services):
    a = services.execute_tool("notes.create", {"title": "A", "body": "Original"}).data
    b = services.execute_tool("tasks.create", {"title": "B"}).data
    services.save_note("A", "Later user edit", a["id"])
    result = services.execute_tool("actions.rollback", {"ids": [a["undo_id"], b["undo_id"]]}, approve=lambda _: True)
    assert not result.ok and result.data["results"][0]["ok"]
    assert result.data["remaining"] == [a["undo_id"]]
    assert not services.list_tasks() and services.list_notes()[0]["body"] == "Later user edit"
    c = services.execute_tool("notes.create", {"title": "C", "body": ""}).data
    services.records.put("task.meta", {"source_note_id": c["id"]})
    assert not services.undo.preview(c["undo_id"])["available"]


def test_browser_evidence_redaction_and_duplicate_cleanup_denial(services, monkeypatch):
    monkeypatch.setattr(services.browser.bridge, "inspect", lambda _: {
        "text": "Guide\nExport with File > Export.\n", "url": "https://example.com/guide", "document": "d1"})
    evidence = services.execute_tool("browser.page_question", {"tab_id": "1", "question": "Where is Export?"})
    assert evidence.ok and evidence.data["items"][0]["citation"]["line"] == 2
    monkeypatch.setattr(services.browser.bridge, "tabs", lambda: [
        {"id": str(i), "title": "Page", "url": url, "dedupe_safe": True} for i, url in enumerate([
            "https://example.com", "https://example.com", "[redacted]", "[redacted]"])])
    assert [r["id"] for r in services.browser.duplicate_tabs()["items"]] == ["1"]
    close = Mock()
    monkeypatch.setattr(services.browser.bridge, "tab_action", close)
    result = services.execute_tool("browser.close_duplicates", {"tab_ids": ["1"]},
                                  approve=lambda request: request.tool_name == "browser.close_duplicates")
    assert not result.ok and not result.data["complete"]
    close.assert_not_called()
    monkeypatch.setattr(services.browser.bridge, "inspect", lambda _: {"blocked": True, "text": "secret"})
    assert not services.execute_tool("browser.page_summary", {"tab_id": "1"}).ok


def test_browser_page_note_is_bounded_and_link_denial_is_partial(services, tmp_path, monkeypatch):
    project = services.add_project("Project", str(tmp_path))
    monkeypatch.setattr(services.browser.bridge, "inspect", lambda _: {
        "url": "https://example.com/" + "a" * 300, "text": "x" * 24000})
    services.permissions.set_grant("notes.organize", "deny")
    result = services.execute_tool("browser.save_page", {"tab_id": "1", "project_id": project})
    assert not result.ok and result.data["stored_locally"] and not result.data["project_linked"]
    assert len(services.list_notes()[0]["body"]) <= 16000


def test_cancel_interrupts_lock_wait_and_stalled_http_body():
    cancel, entered, finished = Event(), Event(), Event()
    lock = Lock()
    lock.acquire()
    def wait_lock():
        with operation(cancel):
            entered.set()
            with pytest.raises(InterruptedError), cancellable_lock(lock):
                pytest.fail("Cancelled waiter acquired the lock")
        finished.set()
    worker = Thread(target=wait_lock)
    worker.start()
    assert entered.wait(2)
    cancel.set()
    assert finished.wait(2)
    lock.release()
    worker.join(2)

    class Stalled(httpx.SyncByteStream):
        def __init__(self):
            self.closed = Event()
        def __iter__(self):
            entered.set()
            assert self.closed.wait(3), "Cancellation did not close the response"
            raise httpx.ReadError("closed")
            yield b""  # protocol generator
        def close(self):
            self.closed.set()
    body = Stalled()
    cancel.clear()
    entered.clear()
    finished.clear()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))) as client:
        def request():
            with operation(cancel), pytest.raises(InterruptedError):
                Transport("test-key", client).request("https://example.com", {}, {})
            finished.set()
        worker = Thread(target=request)
        worker.start()
        assert entered.wait(2)
        cancel.set()
        assert finished.wait(2)
        worker.join(2)
        assert not client.is_closed
