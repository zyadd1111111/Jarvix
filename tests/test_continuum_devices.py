import copy
import json
from types import SimpleNamespace

import pytest

from jarvix.services import Services


class Vault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


@pytest.fixture
def pair(tmp_path):
    pytest.importorskip("cryptography")
    vault, clock = Vault(), [1700000000]
    first = Services(tmp_path / "first", vault=SimpleNamespace(get=lambda _: None))
    second = Services(tmp_path / "second", vault=SimpleNamespace(get=lambda _: None))
    for service in (first, second):
        service.devices._vault_backend = vault
        service.devices._clock = lambda: clock[0]
        service.devices.initialize()
    identities = [service.devices.identity()["identity"] for service in (first, second)]
    for service, identity in ((first, identities[1]), (second, identities[0])):
        service.devices.pair("Reviewed peer", identity, identity["device_id"], ["status", "task_proposal", "note_proposal"])
    yield first, second, vault, clock, identities
    first.close()
    second.close()


def test_authenticated_proposals_use_existing_tool_schema_without_remote_execution_or_content_storage(pair, monkeypatch):
    first, second, vault, _, identities = pair
    monkeypatch.setattr(second, "execute_tool", lambda *args, **kwargs: pytest.fail("Peer instructions must never execute"))
    before = second.db.query("SELECT COUNT(*) AS count FROM notes")[0]["count"]
    sealed = first.devices.seal(identities[1]["device_id"], "note_proposal", {"title": "Review me", "body": "Private peer proposal"})
    assert not sealed["sent"]
    assert "Private peer proposal" not in json.dumps(sealed)
    value = second.devices.receive(sealed["envelope"])
    assert value["accepted"] and not value["executed"] and not value["proposal_content_stored"]
    assert value["proposal"]["tool"] == "notes.create"
    assert value["proposal"]["arguments"]["body"] == "Private peer proposal"
    assert value["proposal"]["content_trust"] == "untrusted_peer_input"
    assert second.db.query("SELECT COUNT(*) AS count FROM notes")[0]["count"] == before
    assert "Private peer proposal" not in json.dumps(first.records.list("device_peer") + second.records.list("device_peer"))
    database_text = json.dumps(second.db.query("SELECT * FROM records"))
    for secret in vault.values.values():
        for name in ("signing", "exchange"):
            assert json.loads(secret)[name] not in database_text


@pytest.mark.parametrize("field", ["ciphertext", "signature", "nonce", "kind"])
def test_tampered_envelope_never_consumes_valid_sequence(pair, field):
    first, second, _, _, identities = pair
    sealed = first.devices.seal(identities[1]["device_id"], "status", {})["envelope"]
    tampered = copy.deepcopy(sealed)
    if field == "kind":
        tampered["header"]["kind"] = "task_proposal"
    elif field == "nonce":
        tampered["header"]["nonce"] = "AAAAAAAAAAAAAAAA"
    else:
        tampered[field] = ("B" if tampered[field][0] == "A" else "A") + tampered[field][1:]
    with pytest.raises(PermissionError, match="authentication"):
        second.devices.receive(tampered)
    assert second.devices.receive(sealed)["accepted"]
    with pytest.raises(PermissionError, match="already received"):
        second.devices.receive(sealed)


def test_replay_counters_and_identity_survive_restart_without_replaying_actions(pair):
    first, second, vault, clock, identities = pair
    envelope = first.devices.seal(identities[1]["device_id"], "task_proposal", {"title": "Prepared work"})["envelope"]
    second.devices.receive(envelope)
    data_dir = second.data_dir
    second.close()
    reopened = Services(data_dir, vault=SimpleNamespace(get=lambda _: None))
    reopened.devices._vault_backend, reopened.devices._clock = vault, lambda: clock[0]
    try:
        assert reopened.devices.identity()["identity"] == identities[1]
        with pytest.raises(PermissionError, match="already received"):
            reopened.devices.receive(envelope)
        next_envelope = first.devices.seal(identities[1]["device_id"], "status", {})["envelope"]
        assert next_envelope["header"]["sequence"] == 2
        assert reopened.devices.receive(next_envelope)["sequence"] == 2
        assert not reopened.list_tasks()
    finally:
        reopened.close()


def test_expiry_capabilities_revocation_and_recipient_are_enforced(pair):
    first, second, _, clock, identities = pair
    sender, recipient = identities[0]["device_id"], identities[1]["device_id"]
    envelope = first.devices.seal(recipient, "status", {}, ttl_seconds=30)["envelope"]
    clock[0] += 31
    with pytest.raises(PermissionError, match="expired"):
        second.devices.receive(envelope)
    envelope = first.devices.seal(recipient, "status", {})["envelope"]
    other = copy.deepcopy(envelope)
    other["header"]["recipient"] = "0" * 64
    with pytest.raises(PermissionError, match="another identity"):
        second.devices.receive(other)
    second.devices.receive(envelope)
    second.devices.revoke(sender)
    fresh = first.devices.seal(recipient, "status", {})["envelope"]
    with pytest.raises(PermissionError, match="revoked"):
        second.devices.receive(fresh)
    second.devices.pair("Repaired", identities[0], sender, ["status"])
    with pytest.raises(PermissionError, match="already received"):
        second.devices.receive(envelope)
    assert second.devices.receive(fresh)["accepted"]
    note = first.devices.seal(recipient, "note_proposal", {"title": "A note", "body": "Review"})["envelope"]
    with pytest.raises(PermissionError, match="capability"):
        second.devices.receive(note)


def test_disabled_or_denied_sources_cannot_prepare_actions_or_expose_projects(pair):
    first, second, _, _, identities = pair
    recipient = identities[1]["device_id"]
    second.permissions.set_grant("notes.create", "deny")
    note = first.devices.seal(recipient, "note_proposal", {"title": "Denied note", "body": "Review"})["envelope"]
    with pytest.raises(PermissionError):
        second.devices.receive(note)
    first.settings.set("tools.enabled", [name for name in first.enabled_tools() if name != "tasks.create"])
    with pytest.raises(PermissionError):
        first.devices.seal(recipient, "task_proposal", {"title": "No task access"})
    second.settings.set("tools.enabled", [name for name in second.enabled_tools() if name != "devices.receive"])
    status = first.devices.seal(recipient, "status", {})["envelope"]
    with pytest.raises(PermissionError):
        second.devices.receive(status)


def test_pairing_and_disclosure_need_fresh_approval_and_recheck_revocation(pair):
    first, second, _, _, identities = pair
    arguments = {"name": "Reviewed peer", "identity": identities[0], "fingerprint": identities[0]["device_id"], "capabilities": ["status"]}
    second.permissions.set_grant("devices.pair", "allow")
    assert not second.execute_tool("devices.pair", arguments).ok
    def revoke_while_approving(_):
        second.permissions.set_grant("devices.identity", "deny")
        return True
    assert not second.execute_tool("devices.pair", arguments, approve=revoke_while_approving).ok
    assert "note_proposal" in second.devices.list()["items"][0]["capabilities"]
    first.permissions.set_grant("devices.seal", "allow")
    sealed = {"device_id": identities[1]["device_id"], "kind": "status", "payload": {}}
    assert not first.execute_tool("devices.seal", sealed).ok
    assert first.execute_tool("devices.seal", sealed, approve=lambda _: True).ok


def test_initialize_is_explicit_and_cannot_pair_mismatched_identity(tmp_path):
    pytest.importorskip("cryptography")
    service = Services(tmp_path, vault=SimpleNamespace(get=lambda _: None))
    vault = Vault()
    service.devices._vault_backend = vault
    try:
        assert not service.devices.identity()["initialized"] and not vault.values
        assert not service.execute_tool("devices.initialize", {}).ok and not vault.values
        value = service.execute_tool("devices.initialize", {}, approve=lambda _: True)
        assert value.ok and value.data["initialized"] and len(vault.values) == 1
        identity = copy.deepcopy(value.data["identity"])
        identity["device_id"] = "0" * 64
        with pytest.raises(ValueError, match="fingerprint"):
            service.devices.pair("Bad peer", identity, identity["device_id"], ["status"])
        with pytest.raises(ValueError, match="own identity"):
            service.devices.pair("Self", value.data["identity"], value.data["fingerprint"], ["status"])
        assert not service.records.list("device_peer")
    finally:
        service.close()


def test_arbitrary_peer_actions_and_oversized_payloads_are_rejected(pair):
    first, _, _, _, identities = pair
    for payload in ({"title": "Task", "tool": "developer.run"}, {"title": "Task", "arguments": {}}, {"title": "x" * 301}):
        with pytest.raises(ValueError):
            first.devices.seal(identities[1]["device_id"], "task_proposal", payload)
    assert first.records.get("device_peer", identities[1]["device_id"])["last_sent_sequence"] == 0
