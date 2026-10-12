"""NEW workspace presentation keeps real state and the existing permission route."""
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from jarvix.ui.nexus_frontend.workspaces import NexusAdaptiveWorkspace, NexusOperatorWorkspace, ExecutionRail
from test_interface_modes import opened_interface
from test_ui import app as app, wait_until


def test_liquid_operator_timeline_uses_saved_verification_and_review(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        session = window.services.operator.run({"goal": "Read today's tasks", "steps": [
            {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
        window.navigate("Operator")
        view = window.pages["Operator"]
        assert isinstance(view, NexusOperatorWorkspace)
        view.open_session(session["id"])
        assert view.steps.count() == 1 and view.progress.value() == 100
        assert isinstance(view.steps.itemDelegate(), ExecutionRail)
        assert view.steps.item(0).data(Qt.ItemDataRole.UserRole)["verified"] == session["steps"][0]["verified"]
        assert view.sessions.isHidden()
        calls = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args=None: calls.append((name, args)))
        view.save_progress_button.click()
        assert calls == [("continuity.save", {"summary": "Read today's tasks", "operator_session_id": session["id"]})]
        assert not window.services.settings.get("control.enabled", False)


def test_liquid_missions_keep_real_progress_resources_and_history(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        task = window.services.add_task("Verify package")
        window.services.productivity.tasks.complete(task)
        mission = window.services.missions.save("Release Jarvix", task_ids=[task])
        window.navigate("Missions")
        view = window.pages["Missions"]
        assert isinstance(view, NexusAdaptiveWorkspace)
        wait_until(app, lambda: not window.jobs and view.current_mission is not None)
        assert view.mission_goal.text() == "Release Jarvix"
        assert view.mission_progress.value() == mission["progress"]["percent"] == 100
        assert view.mission_links.topLevelItemCount() == 1
        assert view.mission_history.topLevelItemCount() >= 1
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        view.refresh_missions()
        wait_until(app, lambda: not window.jobs)
        assert view.mission_metadata.text().endswith("100%")
        calls = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args=None: calls.append((name, args)))
        view.mission_actions["pause"].click()
        assert calls == [("missions.pause", {"id": mission["id"]})]
        assert window.services.missions.get(mission["id"])["status"] == "active"


def test_liquid_knowledge_reads_excerpts_only_on_request_and_clears_revoked_sources(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        note = window.services.save_note("OAuth", "Private local source")
        space = window.services.knowledge_spaces.create("Research")
        window.services.knowledge_spaces.add_source(space["id"], "note", note)
        window.services.knowledge_spaces.refresh(space["id"])
        window.navigate("Knowledge")
        view = window.pages["Knowledge"]
        wait_until(app, lambda: not window.jobs and view.sources.count() == 1)
        assert view.knowledge_split.count() == 3
        assert view.spaces.currentItem().data(Qt.ItemDataRole.UserRole) == space["id"]
        assert "Private local source" not in view.document_view.toPlainText()
        assert view.source_properties.topLevelItem(2).text(1) == note
        assert view.document_title.text() == "OAuth"
        assert view.source_properties.topLevelItem(2).toolTip(1) == note
        view.read_excerpts()
        wait_until(app, lambda: not window.jobs)
        assert "Private local source" in view.document_view.toPlainText()
        assert "[1] OAuth" in view.document_view.toPlainText()
        window.services.permissions.set_grant("knowledge_spaces.summarize", "deny")
        view.read_excerpts()
        wait_until(app, lambda: not window.jobs)
        assert "Could not read excerpts" in view.document_view.toPlainText()
        window.services.permissions.set_grant("knowledge_spaces.get", "deny")
        view.select_space()
        wait_until(app, lambda: not window.jobs)
        assert not view.sources.count() and not view.source_properties.topLevelItemCount()


def test_liquid_skills_use_installed_recipes_and_execution_review(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        services = window.services
        routine = services.workflows.save("Inspect tasks", [
            {"kind": "action", "tool": "tasks.list", "arguments": {}}], kind="routine")["id"]
        preview = services.skills.learn_preview("Task review", routine_id=routine)
        skill = services.execute_tool("skills.save", {"name": preview["name"], "routine_id": routine,
                                      "review_fingerprint": preview["review_fingerprint"]}, approve=lambda _: True).data
        window.navigate("Skills")
        view = window.pages["Skills"].saved_work["Skills"]
        wait_until(app, lambda: not window.jobs and view.items.count() == 1)
        assert view.inspector.title.text() == "Task review"
        assert services.workflows.history(routine) == []
        calls = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args=None: calls.append((name, args)))
        next(control for control in view.controls if control.text() == "Run Skill").click()
        assert calls == [("skills.run", {"id": skill["id"]})]
        assert services.workflows.history(routine) == []


def test_liquid_compact_workspaces_keep_inspectors_reachable(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.resize(860, 600)
        window.navigate("Operator")
        view = window.pages["Operator"]
        app.processEvents()
        assert view.inspector.isHidden() and view.session_picker.isVisible()
        view.show_inspector()
        wait_until(app, lambda: view.inspector.isVisible())
        assert all(control.width() >= control.minimumSizeHint().width()
                   for control in (view.pause_button, view.resume_button, view.cancel_button))
        window.navigate("Knowledge")
        knowledge = window.pages["Knowledge"]
        wait_until(app, lambda: not window.jobs)
        app.processEvents()
        assert knowledge.citation_panel.isHidden()
        knowledge.toggle_citations()
        wait_until(app, lambda: knowledge.citation_panel.isVisible())
        assert knowledge.source_library.isHidden()
        knowledge.toggle_citations()
        wait_until(app, lambda: knowledge.citation_panel.isHidden())
        assert knowledge.spaces.isVisible()
        assert knowledge.sources.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        window.navigate("Missions")
        wait_until(app, lambda: not window.jobs)
        assert window.pages["Missions"] is not knowledge
        assert knowledge.isHidden()


def test_liquid_plan_selection_preserves_dependencies_and_keyboard_navigation(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        session = window.services.operator.run({"goal": "Review local work", "steps": [
            {"id": "tasks", "tool": "tasks.list", "arguments": {}},
            {"id": "notes", "tool": "notes.search", "arguments": {"query": "Jarvix"}, "depends_on": ["tasks"]},
        ]}, approve=lambda _: True)
        window.navigate("Operator")
        view = window.pages["Operator"]
        view.open_session(session["id"])
        view.steps.setFocus()
        QTest.keyClick(view.steps, Qt.Key.Key_Down)
        assert view.steps.currentItem().data(Qt.ItemDataRole.UserRole)["id"] == "notes"
        assert view.steps.currentItem().data(Qt.ItemDataRole.UserRole)["depends_on"] == ["tasks"]
        assert view.steps.accessibleName()
        assert view.step_inspector.topLevelItem(0).text(1) == "notes.search"


def test_liquid_skill_empty_state_and_reduced_motion_inspector(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window._liquid_preferences["motion"] = False
        window.resize(860, 600)
        window.navigate("Skills")
        view = window.pages["Skills"]
        wait_until(app, lambda: not window.jobs)
        assert not view.saved_work["Skills"].items.count()
        assert view.saved_work["Skills"].inspector.title.text() == "Select a Skill"
        window.navigate("Knowledge")
        view = window.pages["Knowledge"]
        wait_until(app, lambda: not window.jobs)
        view.toggle_citations()
        assert view.citation_panel.isVisible()
        assert not getattr(view.citation_panel, "_liquid_animations", {})
