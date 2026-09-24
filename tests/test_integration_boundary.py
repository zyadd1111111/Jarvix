from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event, Thread

import pytest

from jarvix.capabilities.integration import INTEGRATIONS, IntegrationService


@pytest.fixture
def integration():
    vault = Mock()
    service = IntegrationService(SimpleNamespace(repository=Mock()), vault=vault)
    adapter = Mock(integration_id="github")
    adapter.connect.return_value = True
    service.register_adapter(adapter)
    return service, adapter, vault


def test_absent_adapters_are_honestly_not_connected(integration):
    service, _, _ = integration
    rows = service.status()
    assert {row["id"] for row in rows} == set(INTEGRATIONS)
    assert all(row["status"] == "Not connected" for row in rows)
    assert not next(row for row in rows if row["id"] == "gmail")["adapter_available"]
    with pytest.raises(ValueError, match="adapter"):
        service.connect("gmail", "secret")


def test_verified_connection_stores_credential_only_in_vault(integration):
    service, adapter, vault = integration
    assert service.connect("github", "private-token")["status"] == "Connected"
    adapter.connect.assert_called_once_with("private-token")
    vault.set.assert_called_once_with("github", "private-token")
    assert "private-token" not in str(service.s.repository.mock_calls)
    assert "private-token" not in str(service.status())
    assert next(row for row in service.status() if row["id"] == "github")["status"] == "Connected"


@pytest.mark.parametrize("failure", [False, RuntimeError("private-token")])
def test_failed_connection_never_claims_connected_or_leaks_secret(integration, failure):
    service, adapter, vault = integration
    if isinstance(failure, Exception):
        adapter.connect.side_effect = failure
    else:
        adapter.connect.return_value = failure
    with pytest.raises(RuntimeError) as error:
        service.connect("github", "private-token")
    assert "private-token" not in str(error.value)
    assert "private-token" not in str(service.s.repository.mock_calls)
    vault.set.assert_not_called()
    adapter.disconnect.assert_called_once()
    assert all(row["status"] == "Not connected" for row in service.status())


def test_saved_credentials_require_fresh_adapter_verification(integration):
    service, adapter, vault = integration
    vault.get.return_value = "saved-token"
    service.connect("github")
    adapter.connect.assert_called_once_with("saved-token")
    vault.set.assert_not_called()
    service.disconnect("github")
    vault.delete.assert_called_once_with("github")
    adapter.disconnect.assert_called_once()
    assert all(row["status"] == "Not connected" for row in service.status())


def test_vault_failure_rolls_back_verified_session(integration):
    service, adapter, vault = integration
    vault.set.side_effect = RuntimeError("credential failure with private-token")
    with pytest.raises(RuntimeError, match="connection failed"):
        service.connect("github", "private-token")
    adapter.disconnect.assert_called_once()
    assert all(row["status"] == "Not connected" for row in service.status())


def test_close_releases_session_without_erasing_saved_credential(integration):
    service, adapter, vault = integration
    service.connect("github", "token")
    service.close()
    adapter.disconnect.assert_called_once()
    vault.delete.assert_not_called()
    assert all(row["status"] == "Not connected" for row in service.status())


def test_disconnect_during_connection_cannot_reactivate_session(integration):
    service, adapter, vault = integration
    def interrupted(_):
        service.disconnect("github")
        return True
    adapter.connect.side_effect = interrupted
    with pytest.raises(RuntimeError):
        service.connect("github", "token")
    vault.set.assert_not_called()
    assert all(row["status"] == "Not connected" for row in service.status())


def test_simultaneous_reconnect_cannot_discard_another_session(integration):
    service, adapter, vault = integration
    entered, release = Event(), Event()
    errors = []

    def connecting(_):
        entered.set()
        assert release.wait(timeout=3)
        return True

    def run():
        try:
            service.connect("github", "first-token")
        except Exception as exc:
            errors.append(exc)

    adapter.connect.side_effect = connecting
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(timeout=3)
        with pytest.raises(RuntimeError, match="connecting"):
            service.connect("github", "second-token")
        adapter.disconnect.assert_not_called()
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert not errors
    vault.set.assert_called_once_with("github", "first-token")
    assert next(row for row in service.status() if row["id"] == "github")["status"] == "Connected"


def test_shutdown_prevents_new_sessions_and_disconnect_errors_are_sanitized(integration):
    service, adapter, vault = integration
    service.connect("github", "private-token")
    adapter.disconnect.side_effect = RuntimeError("private-token")
    vault.delete.side_effect = RuntimeError("private-token")
    with pytest.raises(RuntimeError, match="incomplete") as error:
        service.disconnect("github")
    assert "private-token" not in str(error.value)
    assert all(row["status"] == "Not connected" for row in service.status())
    service.close()
    with pytest.raises(RuntimeError, match="closed"):
        service.connect("github", "private-token")
