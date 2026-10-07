"""Skills reuse reviewed routines, retain concrete values and never bypass the host."""
import sys
from types import SimpleNamespace

import pytest

from jarvix.capabilities import skills
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    service.add_file_root(str(tmp_path))
    if not hasattr(service, "skills"):
        skills.setup(service, service.registry)
    yield service
    service.close()


def action(tool="notes.create", arguments=None):
    return {"kind": "action", "tool": tool, "arguments": arguments if arguments is not None else {"title": "Daily note", "body": "Concrete text"}}


def completed_note(services):
    result = services.operator.run({"goal": "Create a daily note", "steps": [
        {"id": "note", "tool": "notes.create", "arguments": {"title": "Daily note", "body": "Concrete text"}}]},
        approve=lambda _: True)
    assert result["ok"] and result["steps"][0]["verified"]
    return result["id"]


def save_arguments(preview, **source):
    return {"name": preview["name"], "review_fingerprint": preview["review_fingerprint"], **source}


def test_verified_learning_preserves_arguments_and_requires_preview_and_confirmation(services):
    session = completed_note(services)
    preview = services.skills.learn_preview("Daily note", session_id=session)
    assert preview["routine"]["steps"][0]["arguments"] == {"title": "Daily note", "body": "Concrete text"}
    assert preview["actions_executed"] == 0 and not preview["automatic_execution"]
    assert services.workflows.list() == [] and len(services.list_notes()) == 1
    args = save_arguments(preview, session_id=session)
    assert services.skills.save_preview(**args)["routine"] == preview["routine"]
    denied = services.execute_tool("skills.save", args, approve=lambda _: False)
    assert not denied.ok and services.workflows.list() == [] and services.skills.list()["items"] == []
    saved = services.execute_tool("skills.save", args, approve=lambda _: True)
    assert saved.ok and len(services.list_notes()) == 1
    routine = services.workflows.get(saved.data["routine_id"])
    assert routine["kind"] == "routine" and routine["trigger"] == "manual" and not routine["enabled"]
    assert not services.execute_tool("skills.save", args, approve=lambda _: True).ok
    assert len(services.workflows.list()) == 1


def test_usage_is_actual_routine_history_and_each_run_rechecks_action_permissions(services):
    routine = services.workflows.save("Daily", [action()], kind="routine")["id"]
    preview = services.skills.learn_preview("Daily", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(preview, routine_id=routine), approve=lambda _: True)
    id = saved.data["id"]
    assert services.skills.get(id)["usage"]["recorded_runs"] == 0
    assert not services.execute_tool("skills.run", {"id": id}, approve=lambda _: False).ok
    assert services.workflows.history(routine) == []
    assert services.execute_tool("skills.run", {"id": id}, approve=lambda _: True).ok
    denied = services.execute_tool("skills.run", {"id": id},
                                   approve=lambda request: request.tool_name in {"skills.run", "routines.run"})
    assert not denied.ok and len(services.list_notes()) == 1
    usage = services.skills.get(id)["usage"]
    assert usage["source"] == "linked routine history"
    assert (usage["recorded_runs"], usage["successes"], usage["failures"], usage["success_rate"]) == (2, 1, 1, .5)
    services.skills.set_enabled(id, False)
    assert not services.execute_tool("skills.run", {"id": id}, approve=lambda _: True).ok
    assert services.skills.get(id)["enabled"] is False
    assert services.skills.delete(id)["linked_routine_preserved"]
    assert services.workflows.get(routine)["id"] == routine


def test_source_change_invalidates_preview_and_saved_recipe(services):
    routine = services.workflows.save("Daily", [action()], kind="routine")["id"]
    preview = services.skills.learn_preview("Daily skill", routine_id=routine)
    args = save_arguments(preview, routine_id=routine)
    def change_on_confirmation(request):
        if request.tool_name == "skills.save":
            services.workflows.save("Changed", [action(arguments={"title": "Changed", "body": "Changed"})],
                                    kind="routine", id=routine)
        return True
    assert not services.execute_tool("skills.save", args, approve=change_on_confirmation).ok
    assert services.skills.list()["items"] == []
    reviewed = services.skills.learn_preview("Daily skill", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(reviewed, routine_id=routine), approve=lambda _: True)
    id = saved.data["id"]
    services.workflows.save("Edited in builder", [action()], kind="routine", id=routine)
    assert not services.execute_tool("skills.preview", {"id": id}).ok
    assert not services.execute_tool("skills.run", {"id": id}, approve=lambda _: True).ok
    assert services.workflows.history(routine) == []
    reviewed = services.skills.learn_preview("Daily skill", routine_id=routine, id=id)
    updated = services.execute_tool("skills.save", save_arguments(reviewed, routine_id=routine, id=id), approve=lambda _: True)
    assert updated.ok and updated.data["id"] == id


def test_disabling_skill_during_action_approval_prevents_execution(services):
    routine = services.workflows.save("Daily", [action()], kind="routine")["id"]
    review = services.skills.learn_preview("Daily", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(review, routine_id=routine), approve=lambda _: True)
    id = saved.data["id"]
    def disable_on_action(request):
        if request.tool_name == "notes.create":
            services.skills.set_enabled(id, False)
        return True
    result = services.execute_tool("skills.run", {"id": id}, approve=disable_on_action)
    assert not result.ok and not services.list_notes()


def test_disabled_source_tools_cannot_be_read_through_a_skill_alias(services):
    session = completed_note(services)
    routine = services.workflows.save("Daily", [action()], kind="routine")["id"]
    review = services.skills.learn_preview("Daily", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(review, routine_id=routine), approve=lambda _: True)
    for source, tool, args in (
        ("operator.session", "skills.learn_preview", {"name": "Source", "session_id": session}),
        ("workflows.preview", "skills.learn_preview", {"name": "Source", "routine_id": routine}),
        ("workflows.history", "skills.get", {"id": saved.data["id"]}),
        ("routines.run", "skills.run", {"id": saved.data["id"]}),
    ):
        services.settings.set("tools.enabled", [spec.name for spec in services.registry.specs() if spec.name != source])
        assert not services.execute_tool(tool, args, approve=lambda _: True).ok
    assert services.workflows.history(routine) == []


def test_root_revocation_and_disabled_tools_block_preview_and_run(services, tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("Source")
    routine = services.workflows.save("Inspect", [action("files.inspect", {"path": str(path)})], kind="routine")["id"]
    reviewed = services.skills.learn_preview("Inspect", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(reviewed, routine_id=routine), approve=lambda _: True)
    id = saved.data["id"]
    services.remove_file_root(str(tmp_path))
    assert not services.execute_tool("skills.preview", {"id": id}).ok
    assert not services.execute_tool("skills.run", {"id": id}, approve=lambda _: True).ok
    assert services.workflows.history(routine) == []
    services.add_file_root(str(tmp_path))
    services.settings.set("tools.enabled", [tool.name for tool in services.registry.specs() if tool.name != "files.inspect"])
    assert not services.execute_tool("skills.preview", {"id": id}).ok
    services.settings.set("tools.enabled", [tool.name for tool in services.registry.specs()])
    services.permissions.set_grant("files.inspect", "deny")
    assert not services.execute_tool("skills.preview", {"id": id}).ok


def test_sensitive_original_action_requires_fresh_permission_even_with_saved_grants(services, tmp_path, monkeypatch):
    from jarvix.capabilities import native_windows
    path = tmp_path / "keep.txt"
    path.write_text("Keep")
    reached = []
    monkeypatch.setattr(native_windows, "Win32", lambda: SimpleNamespace(recycle=lambda _: reached.append(True)))
    routine = services.workflows.save("Recycle reviewed file", [action("files.recycle", {"path": str(path)})], kind="routine")["id"]
    preview = services.skills.learn_preview("Recycle reviewed file", routine_id=routine)
    saved = services.execute_tool("skills.save", save_arguments(preview, routine_id=routine), approve=lambda _: True)
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("files.recycle", "allow")
    requested = []
    def approve(request):
        requested.append(request.tool_name)
        return request.tool_name != "files.recycle"
    result = services.execute_tool("skills.run", {"id": saved.data["id"]}, approve=approve)
    assert not result.ok and not reached and path.read_text() == "Keep"
    assert "files.recycle" in requested


@pytest.mark.parametrize("tool, arguments", [
    ("desktop.focus_element", {"element_ref": "transient"}),
    ("browser.selected_text", {"tab_id": "1"}),
    ("developer.command_start", {"argv": [sys.executable, "-c", "print('no')"], "cwd": "C:/"}),
    ("notes.create", {"title": "Credential", "body": "api_key=private-secret"}),
])
def test_transient_control_commands_and_credentials_are_never_learned(services, tool, arguments):
    routine = services.workflows.save("Unsupported", [action(tool, arguments)], kind="routine")["id"]
    result = services.execute_tool("skills.learn_preview", {"name": "Unsupported", "routine_id": routine})
    assert not result.ok and services.skills.list()["items"] == []


def test_failed_unverified_or_undone_operator_steps_are_never_learned(services):
    session = completed_note(services)
    original = services.records.get("operator_session", session)
    for changes in ({"status": "failed"}, {"verified": False}, {"undone": True}):
        altered = {**original, "steps": [{**original["steps"][0], **changes}]}
        services.records.put("operator_session", altered, session)
        assert not services.execute_tool("skills.learn_preview", {"name": "Uncertain", "session_id": session}).ok
    assert services.workflows.list() == []


def test_restart_preserves_skill_and_requires_a_new_learning_review(services):
    session = completed_note(services)
    review = services.skills.learn_preview("Restarted note", session_id=session)
    restarted = Services(services.data_dir, vault=SimpleNamespace(get=lambda _: None))
    if not hasattr(restarted, "skills"):
        skills.setup(restarted, restarted.registry)
    try:
        assert not restarted.execute_tool("skills.save", save_arguments(review, session_id=session), approve=lambda _: True).ok
        fresh = restarted.skills.learn_preview("Restarted note", session_id=session)
        saved = restarted.execute_tool("skills.save", save_arguments(fresh, session_id=session), approve=lambda _: True)
        assert saved.ok and len(restarted.list_notes()) == 1
        assert restarted.skills.preview(saved.data["id"])["actions_executed"] == 0
    finally:
        restarted.close()


def test_structured_recipe_metadata_version_edit_and_duplicate_are_reviewed(services):
    args = {"name": "Release note", "recipe": {"steps": [action()]},
            "description": "Create the reviewed release note", "instructions": "Review the exact text first.",
            "input_schema": {"fields": [{"name": "title", "type": "string", "description": "Documentation only"}]},
            "output_schema": {"fields": [{"name": "note", "type": "object"}]}}
    preview = services.skills.learn_preview(**args)
    assert preview["version"] == 1
    assert preview["required_permissions"][0]["tool"] == "notes.create"
    assert not preview["required_integrations"] and not services.workflows.list()
    save = {**args, "review_fingerprint": preview["review_fingerprint"]}
    assert not services.execute_tool("skills.save", {**save, "instructions": "Changed after review"}, approve=lambda _: True).ok
    original = services.execute_tool("skills.save", save, approve=lambda _: True).data
    duplicate_args = {"name": "Copy", "skill_id": original["id"]}
    clone = services.skills.learn_preview(**duplicate_args)
    duplicate = services.execute_tool("skills.save", {**duplicate_args, "review_fingerprint": clone["review_fingerprint"]},
                                      approve=lambda _: True).data
    assert duplicate["routine_id"] != original["routine_id"] and duplicate["description"] == args["description"]
    edit_args = {"name": "Edited", "id": original["id"], "routine_id": original["routine_id"], "description": "Updated"}
    edit = services.skills.learn_preview(**edit_args)
    assert edit["version"] == 2 and edit["instructions"] == args["instructions"]
    edited = services.execute_tool("skills.save", {**edit_args, "review_fingerprint": edit["review_fingerprint"]}, approve=lambda _: True)
    assert edited.ok and edited.data["version"] == 2
    with pytest.raises(ValueError):
        services.skills.learn_preview(**{**edit_args, "version": 1})
    assert services.skills.get(duplicate["id"])["version"] == 1
    assert not services.list_notes()


def test_portable_skill_import_revalidates_exact_recipe_and_does_not_import_permissions(services, tmp_path):
    path = tmp_path / "document.txt"
    path.write_text("Text")
    args = {"name": "Inspect", "recipe": {"steps": [action("files.inspect", {"path": str(path)})]}}
    review = services.skills.learn_preview(**args)
    original = services.execute_tool("skills.save", {**args, "review_fingerprint": review["review_fingerprint"]}, approve=lambda _: True).data
    exported = services.skills.export(original["id"])
    assert exported["exported_files"] == 0 and not exported["automatic_execution"]
    imported = services.skills.import_preview(exported["payload"], name="Imported")
    assert not imported["permissions_imported"] and len(services.skills.list()["items"]) == 1
    denied = services.execute_tool("skills.save", imported["save_arguments"], approve=lambda _: False)
    assert not denied.ok and len(services.workflows.list()) == 1
    services.remove_file_root(str(tmp_path))
    assert not services.execute_tool("skills.save", imported["save_arguments"], approve=lambda _: True).ok
    assert not services.execute_tool("skills.import_preview", {"payload": exported["payload"]}).ok
    services.add_file_root(str(tmp_path))
    tampered = {**exported["payload"], "grants": {"files.inspect": "allow"}}
    assert not services.execute_tool("skills.import_preview", {"payload": tampered}).ok
    oversized = {**exported["payload"], "metadata": {"instructions": "x" * 40000}}
    assert not services.execute_tool("skills.import_preview", {"payload": oversized}).ok


def test_test_mode_reuses_original_read_only_workflow_debug_and_excludes_metrics(services):
    args = {"name": "Read and note", "recipe": {"steps": [action("tasks.list", {}), action()]}}
    preview = services.skills.learn_preview(**args)
    skill = services.execute_tool("skills.save", {**args, "review_fingerprint": preview["review_fingerprint"]}, approve=lambda _: True).data
    tested = services.execute_tool("skills.run", {"id": skill["id"], "test_mode": True}, approve=lambda _: True)
    assert tested.ok and tested.data["test_mode"]
    assert tested.data["steps"][0]["status"] == "completed"
    assert tested.data["steps"][1]["status"] == "preview_only" and not services.list_notes()
    assert services.skills.get(skill["id"])["usage"]["recorded_runs"] == 0
    services.permissions.set_grant("workflows.debug", "deny")
    assert not services.execute_tool("skills.run", {"id": skill["id"], "test_mode": True}, approve=lambda _: True).ok


def test_explicit_commands_are_previewed_but_never_inferred_or_run_in_test_mode(services, tmp_path):
    command = action("developer.command_start", {"argv": [sys.executable, "-c", "print('reviewed')"], "cwd": str(tmp_path)})
    args = {"name": "Approved test command", "recipe": {"steps": [command]}}
    preview = services.skills.learn_preview(**args)
    assert preview["explicit_commands"] and preview["command_previews"][0]["shell"] is False
    assert preview["command_previews"][0]["argv"][1:] == command["arguments"]["argv"][1:]
    skill = services.execute_tool("skills.save", {**args, "review_fingerprint": preview["review_fingerprint"]}, approve=lambda _: True)
    assert skill.ok and not services.developer.sessions
    tested = services.execute_tool("skills.run", {"id": skill.data["id"], "test_mode": True}, approve=lambda _: True)
    assert tested.ok and tested.data["steps"][0]["status"] == "preview_only" and not services.developer.sessions
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("developer.command_start", "allow")
    requests = []
    def deny_command(request):
        requests.append(request.tool_name)
        return request.tool_name != "developer.command_start"
    assert not services.execute_tool("skills.run", {"id": skill.data["id"]}, approve=deny_command).ok
    assert "developer.command_start" in requests and not services.developer.sessions
    assert not services.execute_tool("skills.learn_preview", {"name": "Inferred", "routine_id": skill.data["routine_id"]}).ok
    assert "accepted start" in services.skills.get(skill.data["id"])["command_outcome_policy"]


def test_patterns_require_three_identical_verified_sessions_and_explicit_conversion(services):
    completed_note(services)
    completed_note(services)
    assert services.skills.patterns()["items"] == []
    third = completed_note(services)
    result = services.skills.patterns()
    assert len(result["items"]) == 1 and not services.skills.list()["items"] and not services.workflows.list()
    pattern = result["items"][0]
    assert pattern["frequency"] == 3 and len(pattern["session_ids"]) == 3
    preview = services.skills.pattern_preview(pattern["id"], "Explicit pattern skill")
    assert preview["actions_executed"] == 0
    assert not services.execute_tool("skills.save", preview["save_arguments"], approve=lambda _: False).ok
    assert not services.skills.list()["items"]
    services.skills.dismiss_pattern(pattern["id"])
    assert services.skills.patterns()["items"] == []
    completed_note(services)
    assert services.skills.patterns()["items"][0]["frequency"] == 4
    services.skills.dismiss_pattern(pattern["id"], never_again=True)
    completed_note(services)
    assert services.skills.patterns()["items"] == []
    original = services.records.get("operator_session", third)
    services.records.put("operator_session", {**original, "steps": [{**original["steps"][0], "verified": False}]}, third)
    assert not services.execute_tool("skills.learn_preview", {"name": "Unverified", "session_id": third}).ok
    services.permissions.set_grant("operator.session", "deny")
    assert services.skills.patterns()["items"] == []
