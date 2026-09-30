from types import SimpleNamespace

import pytest

from jarvix.capabilities import knowledge, search
from jarvix.domain import ToolResult
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    if not hasattr(service, "search"):
        search.setup(service, service.registry)
    if not hasattr(service, "knowledge_spaces"):
        knowledge.setup(service, service.registry)
    yield service
    service.close()


def test_space_refresh_search_question_and_summary_reuse_local_evidence(services):
    path = services.test_root / "plan.md"
    path.write_text("# Launch\nThe launch is Friday\n", encoding="utf-8")
    note = services.save_note("Delivery", "The launch needs a delivery review")
    conversation = services.new_conversation("Launch chat")
    services.repository.append_message(conversation, "user", "Launch budget is approved")
    space = services.knowledge_spaces.create("Launch")
    id = space["id"]
    for kind, reference in (("folder", str(services.test_root)), ("note", note), ("conversation", conversation)):
        services.knowledge_spaces.add_source(id, kind, reference)
    assert services.records.list("search.source") == []
    refreshed = services.knowledge_spaces.refresh(id)
    assert refreshed["indexed"] == 3 and not refreshed["cloud_request"]
    again = services.knowledge_spaces.refresh(id)
    assert again["unchanged"] == 3
    result = services.knowledge_spaces.search(id, "launch")
    assert {item["kind"] for item in result["items"]} == {"note", "conversation", "document"}
    answer = services.knowledge_spaces.question(id, "Friday")
    assert "Friday" in answer["answer"] and answer["evidence"][0]["citation"]["path"] == str(path)
    assert services.knowledge_spaces.question(id, "hippopotamus")["answer"] is None
    overview = services.knowledge_spaces.summarize(id)
    assert len(overview["items"]) == 3 and "verbatim" in overview["method"] and not overview["cloud_request"]


def test_source_removal_and_space_deletion_preserve_originals(services):
    note = services.save_note("Reference", "removable evidence")
    id = services.knowledge_spaces.create("Reference")["id"]
    source = services.knowledge_spaces.add_source(id, "note", note)
    services.knowledge_spaces.refresh(id)
    assert services.knowledge_spaces.search(id, "evidence")["items"]
    services.knowledge_spaces.remove_source(id, source["source_id"])
    assert services.knowledge_spaces.search(id, "evidence")["items"] == []
    services.knowledge_spaces.delete(id)
    assert services.list_notes()[0]["id"] == note
    assert services.knowledge_spaces.list()["items"] == []


def test_roots_revalidated_and_refresh_partial_failure_is_reported(services, tmp_path):
    path = services.test_root / "report.txt"
    path.write_text("quasar evidence", encoding="utf-8")
    id = services.knowledge_spaces.create("Safe")["id"]
    services.knowledge_spaces.add_source(id, "document", str(path))
    with pytest.raises(ValueError):
        services.knowledge_spaces.add_source(id, "folder", str(tmp_path))
    services.knowledge_spaces.refresh(id)
    services.remove_file_root(str(services.test_root))
    assert services.knowledge_spaces.search(id, "quasar")["items"] == []
    result = services.knowledge_spaces.refresh(id)
    assert result["errors"] and services.knowledge_spaces.get(id)["last_refresh"]["failed"] == 1


def test_legacy_collection_import_and_git_repo_sources(services):
    path = services.test_root / "README.md"
    path.write_text("Repository browser control", encoding="utf-8")
    (services.test_root / ".git").mkdir()
    old = services.documents.create_collection("Code", [str(path)])
    imported = services.knowledge_spaces.import_collection(old["id"])
    services.knowledge_spaces.add_source(imported["id"], "repository", str(services.test_root))
    result = services.knowledge_spaces.refresh(imported["id"])
    assert result["indexed"] == 1 and services.documents.list_collections()["items"]
    assert services.knowledge_spaces.search(imported["id"], "browser")["items"]


def test_drive_metadata_uses_permission_boundary_and_never_uploads(services, monkeypatch):
    requests = []

    def metadata(name, arguments, **kwargs):
        requests.append((name, arguments))
        return ToolResult(True, {"id": "drive1", "name": "OAuth proposal", "description": "OAuth scopes", "mimeType": "text/plain"})

    monkeypatch.setattr(services, "execute_tool", metadata)
    monkeypatch.setattr(services.integrations, "status", lambda: [{"id": "google_drive", "status": "Connected",
                                                                 "account": "school@example.com", "scopes": ["drive.readonly"]}])
    id = services.knowledge_spaces.create("Drive")["id"]
    source = services.knowledge_spaces.add_source(id, "drive", "drive1")
    services.knowledge_spaces.refresh(id)
    result = services.knowledge_spaces.search(id, "OAuth")
    assert result["items"][0]["citation"]["metadata_only"]
    assert requests == [("google_drive.metadata", {"file_id": "drive1"})] * 2
    monkeypatch.setattr(services.integrations, "status", lambda: [{"id": "google_drive", "status": "Not connected"}])
    assert services.knowledge_spaces.search(id, "OAuth")["items"] == []
    services.knowledge_spaces.remove_source(id, source["source_id"])
    assert services.records.list("knowledge.drive") == []


def test_drive_denial_and_tool_permissions_are_not_bypassed(services, monkeypatch):
    id = services.knowledge_spaces.create("Protected")["id"]
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: ToolResult(False, error="Permission denied"))
    denied = services.knowledge_spaces.add_source(id, "drive", "drive1")
    assert not denied.ok and services.knowledge_spaces.get(id)["sources"] == []
    assert services.registry.get("knowledge_spaces.refresh").permission_level == 2
    assert services.registry.get("knowledge_spaces.delete").permission_level == 3
    assert services.registry.validate("knowledge_spaces.add_source", {"id": id, "kind": "password", "reference": "x"})


def test_drive_account_switch_omits_previously_indexed_metadata(services, monkeypatch):
    account = {"id": "google_drive", "status": "Connected", "account": "first@example.com", "scopes": ["drive.readonly"]}
    monkeypatch.setattr(services.integrations, "status", lambda: [account])
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: ToolResult(True,
        {"id": "drive1", "name": "Private proposal", "description": "quasar evidence"}))
    id = services.knowledge_spaces.create("Drive")["id"]
    services.knowledge_spaces.add_source(id, "drive", "drive1")
    services.knowledge_spaces.refresh(id)
    assert services.knowledge_spaces.search(id, "quasar")["items"]
    account["account"] = "second@example.com"
    assert services.knowledge_spaces.search(id, "quasar")["items"] == []
    # Explicit refresh through the new account boundary makes that account's result usable.
    assert services.knowledge_spaces.refresh(id)["indexed"] == 1
    assert services.knowledge_spaces.search(id, "quasar")["items"]
    account["scopes"] = []
    assert services.knowledge_spaces.search(id, "quasar")["items"] == []


def test_source_removal_during_semantic_query_is_rechecked(services):
    note = services.save_note("Private", "quasar evidence")
    id = services.knowledge_spaces.create("Local")["id"]
    added = services.knowledge_spaces.add_source(id, "note", note)
    provider = SimpleNamespace(id="local", is_local=True, base_url="http://127.0.0.1",
                               embed=lambda texts, model: [[1., 0.]] * len(texts))
    services.search.embedding_provider = lambda: (provider, "embed")
    services.knowledge_spaces.refresh(id)

    def remove(texts, model):
        services.knowledge_spaces.remove_source(id, added["source_id"])
        return [[1., 0.]]

    provider.embed = remove
    assert services.knowledge_spaces.search(id, "quasar")["items"] == []
