"""Account connector boundary. Adapters register their own permissioned tools."""
from __future__ import annotations

import json
import threading
from typing import Protocol

from jarvix.capabilities.schema import enum, register
from jarvix.security import CredentialVault
from jarvix.domain import ToolResult

from .account_adapters import ADAPTERS, AccountAdapter, AccountError
from .account_oauth import OAuthError, PROVIDERS, authorize

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

    def _valid_key(self, key):
        if key not in INTEGRATIONS and key not in {name + ".client_secret" for name in PROVIDERS}:
            raise ValueError("Unknown integration credential.")

    def get(self, provider):
        self._valid_key(provider)
        # Account credentials are never loaded from environment variables or SQLite.
        try:
            return self._backend().get_password(self.service, provider)
        except Exception:
            return None

    def set(self, provider, value):
        self._valid_key(provider)
        if not isinstance(value, str) or not value.strip() or len(value) > 32768:
            raise ValueError("Invalid integration credential.")
        try:
            self._backend().set_password(self.service, provider, value.strip())
        except Exception:
            raise RuntimeError("The OS credential vault could not save the account credential.") from None

    def delete(self, provider):
        self._valid_key(provider)
        try:
            backend = self._backend()
            if backend.get_password(self.service, provider):
                backend.delete_password(self.service, provider)
        except Exception:
            raise RuntimeError("The OS credential vault could not remove the account credential.") from None


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
        self._cancellations = {}
        self._errors = {}
        self._write_enabled = set()
        self._needs_attention = set()

    def install_defaults(self, client=None):
        """Construct cheap adapters only. Never probe accounts during startup."""
        for kind in ADAPTERS:
            key = kind.integration_id
            self.register_adapter(kind(client=client,
                save_token=lambda value, key=key: self._save_token(key, value),
                get_secret=lambda key=key: self.vault.get(key + ".client_secret") if key in PROVIDERS else None))

    def _save_token(self, integration_id, value):
        with self._lock:
            cancellation = self._cancellations.get(integration_id)
            if self._closed or (cancellation and cancellation.is_set()) or integration_id not in self._connected | self._connecting:
                raise RuntimeError("Account session ended.")
            token = json.loads(value)
            token["_jarvix_write"] = integration_id in self._write_enabled
            self.vault.set(integration_id, json.dumps(token, separators=(",", ":")))

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
                     "status": ("Connecting" if key in self._connecting else
                                "Needs attention" if key in self._needs_attention else
                                "Connected" if key in self._connected else
                                "Error" if key in self._errors and isinstance(self._adapters.get(key), AccountAdapter)
                                else "Not connected"),
                     "adapter_available": key in self._adapters,
                     "account": self._adapters[key].account if isinstance(self._adapters.get(key), AccountAdapter) else "",
                     "scopes": list(self._adapters[key].scopes) if isinstance(self._adapters.get(key), AccountAdapter) else [],
                     "write_enabled": key in self._write_enabled,
                     "last_activity": self._adapters[key].last_activity if isinstance(self._adapters.get(key), AccountAdapter) else None,
                     "error": self._errors.get(key, "")}
                    for key, name in INTEGRATIONS.items()]

    def _begin(self, integration_id, cancel=None):
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
            self._errors.pop(integration_id, None)
            self._needs_attention.discard(integration_id)
            self._cancellations[integration_id] = cancel or threading.Event()
            return adapter, generation

    def connect(self, integration_id, credential=None, *, write=False):
        adapter, generation = self._begin(integration_id)
        return self._verify(integration_id, adapter, generation, credential, write=write)

    def connect_oauth(self, integration_id, client_id, *, client_secret="", write=False, cancel=None,
                      authorize_fn=authorize):
        if integration_id not in PROVIDERS:
            raise ValueError("This integration uses a scoped token, not OAuth.")
        adapter, generation = self._begin(integration_id, cancel)
        try:
            if client_secret:
                self.vault.set(integration_id + ".client_secret", client_secret)
            client_secret = client_secret or self.vault.get(integration_id + ".client_secret") or ""
            token = authorize_fn(integration_id, client_id, client_secret=client_secret, write=write,
                                 cancel=self._cancellations[integration_id])
            return self._verify(integration_id, adapter, generation, json.dumps(token), write=write)
        except (OAuthError, RuntimeError) as exc:
            with self._lock:
                self._connecting.discard(integration_id)
                self._errors[integration_id] = str(exc) if isinstance(exc, OAuthError) else "Account connection failed. Review client settings and retry."
            raise RuntimeError(self._errors[integration_id]) from None

    def _verify(self, integration_id, adapter, generation, credential=None, *, write=False):
        try:
            secret = credential if credential is not None else self.vault.get(integration_id)
            if not isinstance(secret, str) or not secret.strip() or len(secret) > 32768:
                raise ValueError("Missing integration credential.")
            if isinstance(adapter, AccountAdapter):
                bundle = json.loads(secret) if secret.startswith("{") else {"access_token": secret}
                if credential is None:
                    write = bundle.get("_jarvix_write") is True
                if write:
                    self._write_enabled.add(integration_id)
            if adapter.connect(secret) is not True:
                raise RuntimeError("Adapter did not verify the connection.")
            with self._lock:
                if self._generation[integration_id] != generation or self._cancellations[integration_id].is_set():
                    raise RuntimeError("Connection was cancelled.")
                if credential is not None:
                    if isinstance(adapter, AccountAdapter):
                        bundle = {**adapter.token, "_jarvix_write": bool(write)}
                        secret = json.dumps(bundle, separators=(",", ":"))
                    self.vault.set(integration_id, secret)
                self._connected.add(integration_id)
        except Exception as exc:
            try:
                adapter.disconnect()
            except Exception:
                pass
            self.s.repository.audit("integration", f"{integration_id}: connection failed")
            message = str(exc) if isinstance(exc, AccountError) else "Integration connection failed. Check the credential and installed adapter."
            with self._lock:
                self._write_enabled.discard(integration_id)
                self._errors[integration_id] = message
            raise RuntimeError(message) from None
        finally:
            with self._lock:
                self._connecting.discard(integration_id)
        self.s.repository.audit("integration", f"{integration_id}: connected")
        return {"id": integration_id, "status": "Connected"}

    def cancel_connection(self, integration_id):
        with self._lock:
            cancellation = self._cancellations.get(integration_id)
            if cancellation:
                cancellation.set()
            self._generation[integration_id] += 1

    def invoke(self, integration_id, method, arguments, *, write=False):
        """Used by registered, permissioned tools; no arbitrary API paths are accepted."""
        with self._lock:
            if self._closed or integration_id not in self._connected:
                return ToolResult(False, error="Not connected. Connect this account in Integrations.")
            if write and integration_id not in self._write_enabled:
                return ToolResult(False, error="This connection is read-only. Reconnect with write permissions enabled.")
            adapter = self._adapters[integration_id]
        try:
            result = getattr(adapter, method)(**arguments)
            self.s.repository.audit("integration", f"{integration_id}.{method}: completed")
            return ToolResult(True, result)
        except AccountError as exc:
            with self._lock:
                first = integration_id not in self._needs_attention
                self._errors[integration_id] = str(exc)
                if exc.needs_auth:
                    self._needs_attention.add(integration_id)
                    self._connected.discard(integration_id)
            if exc.needs_auth and first and hasattr(self.s, "notifications"):
                self.s.notifications.create(INTEGRATIONS[integration_id] + " needs attention",
                    "Reconnect this account in Integrations.", category="integration")
            self.s.repository.audit("integration", f"{integration_id}.{method}: failed")
            return ToolResult(False, error=str(exc))

    def disconnect(self, integration_id):
        if integration_id not in INTEGRATIONS:
            raise ValueError("Unknown integration.")
        with self._lock:
            self._generation[integration_id] += 1
            self._connected.discard(integration_id)
            self._write_enabled.discard(integration_id)
            self._needs_attention.discard(integration_id)
            self._errors.pop(integration_id, None)
            if integration_id in self._cancellations:
                self._cancellations[integration_id].set()
            adapter = self._adapters.get(integration_id)
        failed = False
        if adapter:
            try:
                adapter.disconnect()
            except Exception:
                failed = True
        try:
            self.vault.delete(integration_id)
            if integration_id in PROVIDERS:
                self.vault.delete(integration_id + ".client_secret")
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
            adapters = [self._adapters[key] for key in self._connected | self._connecting]
            self._connected.clear()
            self._write_enabled.clear()
            for cancellation in self._cancellations.values():
                cancellation.set()
            for key in self._generation:
                self._generation[key] += 1
        for adapter in adapters:
            try:
                adapter.disconnect()
            except Exception:
                pass


def setup(s, registry):
    s.integrations = IntegrationService(s)
    s.integrations.install_defaults()
    register(registry, "integrations.status", "Inspect account connector availability and verified connection status.",
             {}, (), s.integrations.status)
    register(registry, "integrations.disconnect", "Disconnect an account and remove its saved integration credential.",
             {"integration_id": enum(*INTEGRATIONS)}, ("integration_id",),
             s.integrations.disconnect, 3, "integration.credentials")
    from .account_tools import register_accounts
    register_accounts(s, registry)
