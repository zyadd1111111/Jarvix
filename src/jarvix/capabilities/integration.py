"""Account connector boundary. Adapters register their own permissioned tools."""
from __future__ import annotations

import threading
from typing import Protocol

from jarvix.capabilities.schema import enum, register
from jarvix.security import CredentialVault

INTEGRATIONS = {
    "gmail": "Gmail", "google_calendar": "Google Calendar", "google_drive": "Google Drive",
    "github": "GitHub", "spotify": "Spotify", "discord": "Discord",
}


class IntegrationAdapter(Protocol):
    integration_id: str

    def connect(self, credential: str) -> bool:
        """Verify credentials with the service; return True only after success."""
        ...

    def disconnect(self) -> None:
        """Release the local account session; revoke remote access when supported."""
        ...


class IntegrationVault(CredentialVault):
    service = "Jarvix.Integrations"
    env_names = {name: "JARVIX_" + name.upper() + "_CREDENTIAL" for name in INTEGRATIONS}


class IntegrationService:
    def __init__(self, services, vault=None):
        self.s = services
        self.vault = vault if vault is not None else IntegrationVault()
        self._adapters: dict[str, IntegrationAdapter] = {}
        self._connected: set[str] = set()
        self._connecting: set[str] = set()
        self._lock = threading.RLock()
        self._generation = dict.fromkeys(INTEGRATIONS, 0)
        self._closed = False

    def register_adapter(self, adapter: IntegrationAdapter):
        integration_id = adapter.integration_id
        if integration_id not in INTEGRATIONS:
            raise ValueError("Unknown integration.")
        if not callable(getattr(adapter, "connect", None)) or not callable(getattr(adapter, "disconnect", None)):
            raise ValueError("Integration adapter must implement connect and disconnect.")
        with self._lock:
            if self._closed:
                raise RuntimeError("Integration service is closed.")
            if integration_id in self._adapters:
                raise ValueError("An adapter is already registered for this integration.")
            self._adapters[integration_id] = adapter

    def status(self):
        with self._lock:
            return [{"id": key, "name": name,
                     "status": "Connected" if key in self._connected else "Not connected",
                     "adapter_available": key in self._adapters}
                    for key, name in INTEGRATIONS.items()]

    def connect(self, integration_id, credential=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Integration service is closed.")
            adapter = self._adapters.get(integration_id)
            if adapter is None:
                raise ValueError("Install a supported adapter for this integration first.")
            if integration_id in self._connecting or integration_id in self._connected:
                raise RuntimeError("This integration is connecting or already connected. Disconnect before reconnecting.")
            self._connecting.add(integration_id)
            self._generation[integration_id] += 1
            generation = self._generation[integration_id]
            self._connected.discard(integration_id)
        try:
            secret = credential if credential is not None else self.vault.get(integration_id)
            if not isinstance(secret, str) or not secret.strip() or len(secret) > 4096:
                raise ValueError("Missing integration credential.")
            if adapter.connect(secret) is not True:
                raise RuntimeError("Adapter did not verify the connection.")
            with self._lock:
                if self._generation[integration_id] != generation:
                    raise RuntimeError("Connection was cancelled.")
                if credential is not None:
                    self.vault.set(integration_id, secret)
                self._connected.add(integration_id)
        except Exception:
            try:
                adapter.disconnect()
            except Exception:
                pass
            self.s.repository.audit("integration", f"{integration_id}: connection failed")
            raise RuntimeError("Integration connection failed. Check the credential and installed adapter.") from None
        finally:
            with self._lock:
                self._connecting.discard(integration_id)
        self.s.repository.audit("integration", f"{integration_id}: connected")
        return {"id": integration_id, "status": "Connected"}

    def disconnect(self, integration_id):
        if integration_id not in INTEGRATIONS:
            raise ValueError("Unknown integration.")
        with self._lock:
            self._generation[integration_id] += 1
            self._connected.discard(integration_id)
            adapter = self._adapters.get(integration_id)
        failed = False
        if adapter:
            try:
                adapter.disconnect()
            except Exception:
                failed = True
        try:
            self.vault.delete(integration_id)
        except Exception:
            failed = True
        self.s.repository.audit("integration", f"{integration_id}: local disconnection {'incomplete' if failed else 'complete'}")
        if failed:
            raise RuntimeError("Local disconnection was incomplete. Check the credential vault and adapter.") from None
        return {"id": integration_id, "status": "Not connected"}

    def close(self):
        """Release sessions at shutdown; keep vault credentials for explicit reconnect."""
        with self._lock:
            self._closed = True
            adapters = [self._adapters[key] for key in self._connected]
            self._connected.clear()
            for key in self._generation:
                self._generation[key] += 1
        for adapter in adapters:
            try:
                adapter.disconnect()
            except Exception:
                pass


def setup(s, registry):
    s.integrations = IntegrationService(s)
    register(registry, "integrations.status", "Inspect account connector availability and verified connection status.",
             {}, (), s.integrations.status)
    register(registry, "integrations.disconnect", "Disconnect an account and remove its saved integration credential.",
             {"integration_id": enum(*INTEGRATIONS)}, ("integration_id",),
             s.integrations.disconnect, 3, "integration.credentials")
