import os
import threading
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent

from jarvix.domain import Completion, Message, ProviderError
from jarvix.services import Services
from jarvix.ui.adaptive import AdaptiveDialog
from jarvix.ui.window import MainWindow
from jarvix.providers.local import ModelInfo
from test_ui import app as app
from test_ui import window as window, wait_until


class NoVault:
    def get(self, _):
        return None


def test_local_only_blocks_cloud_and_preserves_conversation(tmp_path):
    s = Services(tmp_path, vault=NoVault())
    s.settings.set("ai.local_only", True)
    id = s.new_conversation()
    response = s.chat("Test", id, "openai", "gpt-4.1-mini", lambda _: False, lambda *_: None, threading.Event())
    assert "blocks cloud" in response
    assert len(s.conversation_messages(id)) == 2
    s.close()


def test_local_chat_no_vault_or_cloud_disclosure(tmp_path, monkeypatch):
    s = Services(tmp_path, vault=NoVault())
    class Local:
        id = "ollama"
        is_local = True
        def model_info(self, model):
            return None
        def complete(self, messages, tools, model):
            return Completion(Message("assistant", "Local response"))
        def close(self):
            pass
    monkeypatch.setattr(s, "make_provider", lambda _: Local())
    id = s.new_conversation()
    assert s.chat("Test", id, "ollama", "installed", lambda _: False, lambda *_: None, threading.Event()) == "Local response"
    s.close()


def test_knowledge_and_models_dialog_real_sources(tmp_path, monkeypatch, app):
    monkeypatch.setattr(MainWindow, "refresh_system", lambda _: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda _: None)
    s = Services(tmp_path, vault=NoVault())
    window = MainWindow(s)
    note = s.save_note("OAuth setup", "OAuth browser bridge details")
    space = s.knowledge_spaces.create("Jarvix")
    s.knowledge_spaces.add_source(space["id"], "note", note)
    dialog = AdaptiveDialog(window, "Knowledge")
    wait_until(app, lambda: not window.jobs and dialog.spaces.count() == 1)
    dialog.spaces.setCurrentRow(0)
    wait_until(app, lambda: not window.jobs and dialog.sources.count() == 1)
    assert dialog.sources.count() == 1
    assert window.pages["Chat"].provider.findData("ollama") >= 0
    assert window.pages["Settings"].local_only is not None
    assert len(window.pages["Integrations"].cards) == 6
    assert window.pages["Voice"].input_panel.wake_panel is not None
    assert not s.wake_word.state()["active"]
    dialog.close()
    dialog.deleteLater()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    s.close()
    app.processEvents()


def test_unavailable_embedding_role_does_not_fall_back_to_cloud(tmp_path):
    s = Services(tmp_path, vault=NoVault())
    s.configure_model_role("embeddings", "openai", "unknown")
    try:
        s.embedding_provider()
    except (PermissionError, ProviderError):
        pass
    else:
        raise AssertionError("Cloud embeddings were allowed")
    s.close()


def test_router_enriches_configured_local_models_and_rejects_unknown_cloud_capabilities(tmp_path, monkeypatch):
    s = Services(tmp_path, vault=NoVault())
    closed = []
    class Local:
        def health(self):
            return {"available": True, "models": [{"id": "installed", "capabilities": []}]}
        def model_info(self, model):
            return ModelInfo(model, "ollama", frozenset({"completion", "tools"}), 4096)
        def close(self):
            closed.append(True)
    s.settings.set("model.ollama", "installed")
    s.configure_model_role("planning", "ollama", "installed")
    monkeypatch.setattr(s, "make_provider", lambda _: Local())
    choice = s.route_model("planning", context_tokens=100)
    assert choice["provider"] == "ollama" and "tools" in choice["capabilities"]
    assert len(closed) == 2
    s.configure_model_cost("ollama", "installed", 1)
    s.configure_model_cost("local", "installed", .1)
    s.settings.set("model.local", "installed")
    s.settings.set("routing.cost_preference", "low")
    s.settings.set("routing.prefer_local", False)
    assert s.route_model("chat")["provider"] == "local"
    s.vault = type("Vault", (), {"get": lambda _, provider: "key" if provider == "openai" else None})()
    s.settings.set("model.openai", "unknown-custom")
    s.configure_model_role("vision", "openai", "unknown-custom")
    with pytest.raises(ProviderError):
        s.model_router().select("vision")
    s.close()


def test_cancel_prevents_provider_discovery_and_persists_final_status(tmp_path, monkeypatch):
    s = Services(tmp_path, vault=NoVault())
    cancel = threading.Event()
    cancel.set()
    called = []
    monkeypatch.setattr(s, "route_model", lambda *args, **kwargs: called.append(True))
    id = s.new_conversation()
    answer = s.chat("Plan a task", id, "auto", "", lambda _: False, lambda *_: None, cancel)
    assert "stopped" in answer and not called
    assert len(s.conversation_messages(id)) == 2
    s.close()


def test_workflow_editor_preserves_references_variables_and_blocks(tmp_path, monkeypatch, app):
    from jarvix.ui.workflows import WorkflowBuilder, WorkflowBlockDialog
    monkeypatch.setattr(MainWindow, "refresh_system", lambda _: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda _: None)
    s = Services(tmp_path, vault=NoVault())
    window = MainWindow(s)
    step = {"kind": "action", "tool": "notes.create", "id": "note", "arguments": {
        "title": "Source", "body": {"$ref": "variables.topic"}}}
    block = WorkflowBlockDialog(s, step, window)
    assert block.structured.isChecked()
    block.save()
    assert block.value["arguments"] == step["arguments"] and block.value["id"] == "note"
    definition = {"name": "Adaptive flow", "trigger": "manual", "variables": {"topic": "OAuth"},
        "steps": [{"kind": "set", "name": "topic", "value": "Jarvix"}, step]}
    builder = WorkflowBuilder(window, definition)
    values = builder.values()
    assert values["variables"] == definition["variables"] and values["steps"] == definition["steps"]
    s.workflows.preview_definition(**values)
    block.close()
    builder.close()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    s.close()


def test_fusion_missions_use_saved_progress_and_existing_action_forms(window, app, tmp_path, monkeypatch):
    s = window.services
    s.add_file_root(str(tmp_path))
    project = s.add_project("Jarvix", str(tmp_path))
    task = s.add_task("Verify Fusion")
    mission = s.missions.save("Finish Jarvix 0.7", project_id=project, task_ids=[task], notes="Use existing tools")
    window.open_adaptive("Missions")
    dialog = window.adaptive_dialog
    wait_until(app, lambda: not window.jobs and "Finish Jarvix" in dialog.mission_details.toPlainText())
    assert dialog.missions.count() == 1
    assert "0/1" in dialog.mission_details.toPlainText() and "Use existing tools" in dialog.mission_details.toPlainText()
    assert not dialog.isModal()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    dialog.mission_tool("missions.pause")
    dialog.mission_tool("intelligence.prepare")
    dialog.mission_graph()
    assert calls == [("missions.pause", {"id": mission["id"]}),
                     ("intelligence.prepare", {"mission_id": mission["id"], "goal": "Continue Finish Jarvix 0.7"}),
                     ("context.graph", {"kind": "mission", "reference": mission["id"], "depth": 1})]
    assert s.missions.get(mission["id"])["status"] == "active"
    window.open_adaptive("Models")
    assert window.adaptive_dialog is dialog
    dialog.close()


def test_fusion_suggestions_are_opt_in_explainable_and_never_execute(window, app, monkeypatch):
    s = window.services
    s.records.put("operator_session", {"goal": "Unfinished", "status": "paused", "steps": []}, "unfinished")
    window.open_adaptive("Suggestions")
    dialog = window.adaptive_dialog
    wait_until(app, lambda: not window.jobs)
    assert "Suggestions off" in dialog.suggestion_status.text() and dialog.suggestions.count() == 0
    assert not s.settings.get("proactive.enabled", False)
    s.proactive.configure(True)
    dialog.refresh_suggestions()
    wait_until(app, lambda: not window.jobs)
    assert dialog.suggestions.count() == 1 and "Why this appeared" in dialog.suggestion_details.toPlainText()
    selected = dialog.selected_suggestion()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    dialog.open_suggestion()
    dialog.dismiss_selected_suggestion()
    dialog.mute_selected_category()
    assert calls == [("operator.session", {"id": "unfinished"}),
                     ("suggestions.dismiss", {"id": selected["id"]}),
                     ("suggestions.configure", {"enabled": True, "muted": ["unfinished_session"]})]
    assert s.records.get("operator_session", "unfinished")["status"] == "paused"
    assert not s.records.get("suggestion", selected["id"]).get("dismissed", False)
    dialog.close()


def test_fusion_palette_and_chat_show_real_model_roles(window, tmp_path, monkeypatch):
    from jarvix.ui.window import CommandPalette
    s = window.services
    s.add_file_root(str(tmp_path))
    project = s.add_project("Jarvix", str(tmp_path))
    mission = s.missions.save("Finish Fusion", project_id=project)
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    palette = CommandPalette(window)
    entry = next(entry for entry in palette.entries if entry[:2] == ("Continue Jarvix", "Project"))
    entry[2]()
    assert calls[-1] == ("intelligence.prepare", {"project_id": project, "goal": "Continue Jarvix"})
    entry = next(entry for entry in palette.entries if entry[:2] == ("Finish Fusion", "Mission"))
    entry[2]()
    assert calls[-1] == ("missions.summary", {"id": mission["id"]})
    assert {entry[0] for entry in palette.entries} >= {"Missions", "Local suggestions", "Context graph"}
    chat = window.pages["Chat"]
    assert chat.task_role.findText("coding") >= 0 and chat.task_role.findText("document_analysis") >= 0
    chat.on_activity("model_selected", {"provider": "ollama", "model": "installed", "attempt": 1})
    assert "ollama · installed" in chat.connection.text()
    assert chat.timeline.count() == 1 and "ollama · installed" in chat.timeline.item(0).text()
    palette.close()
