"""Device replay state is serialized across independent instances of one profile."""
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest

from jarvix.services import Services
from test_continuum_devices import Vault, pair as pair


def shared_instance(service, vault, clock):
    shared = Services(service.data_dir, vault=SimpleNamespace(get=lambda _: None))
    shared.devices._vault_backend = vault
    shared.devices._clock = lambda: clock[0]
    return shared


def race(monkeypatch, owner, gate_name, first, second):
    """Hold the first operation after a real read while a second instance acts."""
    entered, release, competing = threading.Event(), threading.Event(), threading.Event()
    original = getattr(owner, gate_name)
    def gated(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "Device operation stranded behind the test gate"
        return result
    monkeypatch.setattr(owner, gate_name, gated)
    def call(function):
        try:
            return function()
        except PermissionError:
            return "denied"
    def contender():
        competing.set()
        return call(second)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(call, first)
        try:
            assert entered.wait(3)
            two = pool.submit(contender)
            assert competing.wait(3)
            # The old read/put implementation can finish the competing update
            # here; the shared writer transaction waits for the first commit.
            try:
                two.result(timeout=.25)
            except TimeoutError:
                pass
        finally:
            release.set()
        return one.result(timeout=5), two.result(timeout=5)


def test_same_envelope_is_accepted_only_once_across_profile_instances(pair, monkeypatch):
    first, second, vault, clock, identities = pair
    shared = shared_instance(second, vault, clock)
    try:
        envelope = first.devices.seal(identities[1]["device_id"], "task_proposal", {"title": "Review once"})["envelope"]
        results = race(monkeypatch, second.devices, "_payload", lambda: second.devices.receive(envelope),
                       lambda: shared.devices.receive(envelope))
        assert sum(isinstance(result, dict) and result["accepted"] for result in results) == 1
        assert results.count("denied") == 1
        assert not second.list_tasks()
    finally:
        shared.close()


def test_sender_sequences_are_unique_across_profile_instances(pair, monkeypatch):
    first, second, vault, clock, identities = pair
    shared = shared_instance(first, vault, clock)
    recipient = identities[1]["device_id"]
    try:
        results = race(monkeypatch, first.devices, "_key", lambda: first.devices.seal(recipient, "status", {}),
                       lambda: shared.devices.seal(recipient, "status", {}))
        assert {result["envelope"]["header"]["sequence"] for result in results} == {1, 2}
    finally:
        shared.close()


@pytest.mark.parametrize("operation", ["seal", "receive", "pair"])
def test_peer_revocation_cannot_be_overwritten_by_an_older_operation(pair, monkeypatch, operation):
    first, second, vault, clock, identities = pair
    sender, recipient = (item["device_id"] for item in identities)
    if operation == "receive":
        owner, peer = second, sender
        envelope = first.devices.seal(recipient, "task_proposal", {"title": "Review"})["envelope"]
        gate = "_payload"
    else:
        owner, peer = first, recipient
        gate = "_key"
    def call():
        if operation == "receive":
            return second.devices.receive(envelope)
        if operation == "seal":
            return first.devices.seal(recipient, "status", {})
        return first.devices.pair("Updated pairing", identities[1], recipient, ["status"])
    shared = shared_instance(owner, vault, clock)
    try:
        race(monkeypatch, owner.devices, gate, call, lambda: shared.devices.revoke(peer))
        row = owner.records.get("device_peer", peer)
        assert row["revoked"]
        if operation == "receive":
            assert row["last_received_sequence"] == 1
        elif operation == "seal":
            assert row["last_sent_sequence"] == 1
    finally:
        shared.close()


def test_concurrent_initialize_generates_one_vault_identity(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    vault = Vault()
    first = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    second = shared_instance(first, vault, [1700000000])
    first.devices._vault_backend = vault
    writes = []
    original = vault.set_password
    def store(*args):
        writes.append(args)
        return original(*args)
    monkeypatch.setattr(vault, "set_password", store)
    try:
        results = race(monkeypatch, vault, "get_password", first.devices.initialize, second.devices.initialize)
        assert len(writes) == 1
        assert results[0]["identity"] == results[1]["identity"]
    finally:
        first.close()
        second.close()
