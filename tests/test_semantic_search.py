import threading
from types import SimpleNamespace

import pytest

from jarvix.capabilities import search
from jarvix.domain import ProviderError
from jarvix.runtime import operation
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
    yield service
    service.close()


class Embeddings:
    is_local, id, base_url = True, "local", "http://127.0.0.1:1234"

    def __init__(self):
        self.calls = []

    def embed(self, texts, model):
        self.calls.append((texts, model))
        return [[1., 0.] if any(word in text.casefold() for word in ("couch", "sofa", "seating"))
                else [0., 1.] for text in texts]


def test_keyword_index_cites_every_supported_local_kind_and_never_claims_semantics(services):
    note = services.save_note("OAuth notes", "Browser control uses OAuth scopes")
    services.add_memory("OAuth scopes should be minimal")
    services.add_task("Review OAuth implementation")
    services.add_project("OAuth project", str(services.test_root))
    conversation = services.new_conversation("OAuth discussion")
    services.repository.append_message(conversation, "user", "OAuth browser authorization")
    document = services.test_root / "report.md"
    document.write_text("# OAuth architecture\nScoped OAuth access\n", encoding="utf-8")
    code = services.test_root / "oauth.py"
    code.write_text("# OAuth implementation\n", encoding="utf-8")
    result = services.search.rebuild(paths=[str(document), str(code)])
    assert result["mode"] == "keyword" and result["indexed"] == 7
    found = services.search.query("OAuth")
    assert {item["kind"] for item in found["items"]} == set(search.KINDS) - {"drive"}
    assert found["mode"] == "keyword" and found["warning"] and not found["cloud_request"]
    assert all(item["semantic_score"] is None and item["citation"]["revision"] for item in found["items"])
    assert next(item for item in found["items"] if item["kind"] == "note")["source_id"] == note


def test_hybrid_ranking_uses_actual_local_vectors_and_skips_unchanged_sources(services):
    provider = Embeddings()
    services.search.embedding_provider = lambda: (provider, "embed-model")
    services.save_note("Furniture", "The couch is blue")
    services.save_note("Commute", "Bicycle lanes")
    first = services.search.rebuild(kinds=["note"])
    assert first["mode"] == "hybrid" and len(provider.calls) == 2
    second = services.search.rebuild(kinds=["note"])
    assert second["unchanged"] == 2 and second["indexed"] == 0 and len(provider.calls) == 2
    found = services.search.query("seating")
    assert found["mode"] == "hybrid" and found["items"][0]["title"] == "Furniture"
    assert found["items"][0]["semantic_score"] == 1 and found["items"][0]["keyword_score"] == 0
    assert found["items"][0]["citation"]["source"] == "note"


def test_changed_deleted_and_revoked_sources_never_return_cached_evidence(services):
    note = services.save_note("Private", "quasar original")
    path = services.test_root / "private.txt"
    path.write_text("quasar document", encoding="utf-8")
    services.search.rebuild(kinds=["note", "document"], paths=[str(path)])
    assert len(services.search.query("quasar")["items"]) == 2
    services.save_note("Private", "different content", note)
    assert {item["kind"] for item in services.search.query("quasar")["items"]} == {"document"}
    services.remove_file_root(str(services.test_root))
    result = services.search.query("quasar")
    assert result["items"] == [] and result["stale_sources"] == 2
    services.search.rebuild(kinds=["note"])
    services.delete_note(note)
    assert services.search.query("different")["items"] == []


def test_embedding_failure_falls_back_but_cloud_provider_is_refused(services):
    services.save_note("Plan", "banana project")
    provider = Embeddings()
    provider.embed = lambda texts, model: [[float("nan")]] * len(texts)
    services.search.embedding_provider = lambda: (provider, "embed-model")
    result = services.search.rebuild(kinds=["note"])
    assert result["mode"] == "keyword" and result["warnings"]
    assert services.search.query("banana")["mode"] == "keyword"
    provider.is_local = False
    with pytest.raises(ValueError, match="local provider"):
        services.search.rebuild(kinds=["note"])


def test_cancellation_does_not_publish_partial_source_or_orphan_chunks(services):
    services.save_note("Plan", "couch project")
    cancelled = threading.Event()
    provider = Embeddings()

    def cancel(texts, model):
        cancelled.set()
        return [[1., 0.]] * len(texts)

    provider.embed = cancel
    services.search.embedding_provider = lambda: (provider, "embed-model")
    with pytest.raises(InterruptedError), operation(cancel=cancelled):
        services.search.rebuild(kinds=["note"])
    assert services.records.list("search.chunk") == [] and services.records.list("search.source") == []


def test_root_revocation_during_embedding_query_is_rechecked_before_disclosure(services):
    path = services.test_root / "couch.txt"
    path.write_text("couch private information", encoding="utf-8")
    provider = Embeddings()
    services.search.embedding_provider = lambda: (provider, "embed-model")
    services.search.rebuild(paths=[str(path)], kinds=["document"])

    def revoke(texts, model):
        services.remove_file_root(str(services.test_root))
        return [[1., 0.]]

    provider.embed = revoke
    assert services.search.query("seating")["items"] == []


def test_search_permission_schema_bounds_and_explicit_index_removal(services):
    note = services.save_note("Plan", "scoped project")
    denied = services.execute_tool("search.rebuild", {"kinds": ["note"]}, approve=lambda _: False)
    assert not denied.ok and services.records.list("search.source") == []
    assert services.execute_tool("search.rebuild", {"kinds": ["note"]}, approve=lambda _: True).ok
    assert not services.execute_tool("search.query", {"query": "project", "limit": 0}).ok
    assert services.search.remove("note", note)["removed"]
    assert services.list_notes()[0]["id"] == note and services.records.list("search.chunk") == []


def test_expired_memory_is_not_indexed_or_disclosed(services):
    memory = services.add_memory("quasar memory")
    services.search.rebuild(kinds=["memory"])
    services.records.put("memory.meta", {"expires_at": "2000-01-01T00:00:00+00:00"}, memory)
    assert services.search.query("quasar")["items"] == []


def test_embedding_lifecycle_crash_cleanup_and_offline_keyword_recovery(services):
    services.save_note("Plan", "couch project")
    orphan = services.records.put("search.chunk", {"source_key": "unpublished", "text": "partial"})
    provider = Embeddings()
    closed = []
    provider.close = lambda: closed.append(True)
    services.search.embedding_provider = lambda: (provider, "embed-model")
    services.search.rebuild(kinds=["note"])
    assert orphan not in {item["id"] for item in services.records.list("search.chunk")}
    services.search.status()
    services.search.query("couch")
    assert len(closed) == 2

    def offline():
        raise ProviderError("Local model unavailable.")

    services.search.embedding_provider = offline
    result = services.search.query("couch")
    assert result["items"] and result["mode"] == "keyword" and result["warning"]


def test_index_status_does_not_contact_embedding_endpoint(services):
    services.settings.set("routing.roles", {"embeddings": {"provider": "ollama", "model": "embed"}})
    services.search.embedding_provider = lambda: pytest.fail("Status must not create or contact providers")
    status = services.search.status()
    assert status["semantic_configured"] and status["embedding_backend"] == "Configured, unchecked"
    assert status["embedding_model"] == "embed"


def test_embedding_cancellation_closes_provider_and_huge_vectors_are_rejected(services):
    services.save_note("Plan", "couch project")
    cancel = threading.Event()
    closed = []
    provider = Embeddings()
    provider.close = lambda: closed.append(True)
    provider.embed = lambda texts, model: cancel.set() or [[1., 0.]] * len(texts)
    services.search.embedding_provider = lambda: (provider, "embed-model")
    with pytest.raises(InterruptedError), operation(cancel=cancel):
        services.search.rebuild(kinds=["note"])
    assert closed == [True]
    with pytest.raises(ValueError, match="invalid vector"):
        search.vector([10**1000, 1])
