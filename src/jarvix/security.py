"""Vault-only secret persistence and explicit, tool-scoped permission decisions."""
from __future__ import annotations

import os

import keyring

from jarvix.domain import Approval, PermissionRequest, ToolSpec
from jarvix.storage import Database, Repository, SettingsRepository, now_iso


class CredentialVault:
    service = "Jarvix"
    env_names = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}

    def _backend(self):
        backend = keyring.get_keyring()
        secure_modules = ("keyring.backends.Windows", "keyring.backends.macOS",
                          "keyring.backends.SecretService", "keyring.backends.libsecret")
        if type(backend).__module__.startswith("keyring.backends.chainer"):
            backend = next((item for item in backend.backends
                            if type(item).__module__.startswith(secure_modules)), None)
        if backend is None or not type(backend).__module__.startswith(secure_modules):
            raise RuntimeError("No supported OS credential vault is available. Configure an API key in the environment.")
        return backend

    def get(self, provider: str) -> str | None:
        if provider not in self.env_names:
            raise ValueError("Unknown provider")
        environment = os.environ.get(self.env_names[provider], "").strip()
        if environment:
            return environment
        try:
            return self._backend().get_password(self.service, provider)
        except Exception:
            return None

    def set(self, provider: str, value: str) -> None:
        if provider not in self.env_names or not value.strip() or len(value) > 4096:
            raise ValueError("Select a provider and enter a valid API key.")
        try:
            self._backend().set_password(self.service, provider, value.strip())
        except Exception as exc:
            raise RuntimeError("The OS credential vault could not save the key.") from exc

    def delete(self, provider: str) -> None:
        if provider not in self.env_names:
            raise ValueError("Unknown provider")
        try:
            backend = self._backend()
            if backend.get_password(self.service, provider):
                backend.delete_password(self.service, provider)
        except Exception as exc:
            raise RuntimeError("The OS credential vault could not remove the key.") from exc


class PermissionService:
    def __init__(self, db: Database, repository: Repository):
        self.db, self.repository = db, repository

    def set_grant(self, tool_name: str, decision: str | None) -> None:
        if decision is None:
            self.db.execute("DELETE FROM grants WHERE tool_name=?", (tool_name,))
        elif decision in {"allow", "deny"}:
            self.db.execute("INSERT INTO grants VALUES (?,?,?) ON CONFLICT(tool_name) DO UPDATE SET decision=excluded.decision,updated_at=excluded.updated_at",
                            (tool_name, decision, now_iso()))
        else:
            raise ValueError("Invalid permission decision")

    def authorize(self, spec: ToolSpec, arguments: dict, approve: Approval) -> bool:
        settings = SettingsRepository(self.db)
        gates = {"clipboard": "clipboard.enabled", "screen": "screenshots.enabled",
                 "microphone": "microphone.enabled"}
        prefix = spec.permission.split(".")[0]
        if prefix in gates and not settings.get(gates[prefix], False):
            self.repository.audit("permission", f"{spec.name}: access switch is off")
            return False
        grants = self.db.query("SELECT decision FROM grants WHERE tool_name=?", (spec.name,))
        if grants and grants[0]["decision"] == "deny":
            allowed = False
        elif spec.permission_level == 3:
            # Stored grants and the normal-control switch NEVER authorize sensitive actions.
            allowed = bool(approve(PermissionRequest("execute", spec.name, spec.permission,
                "Sensitive action — confirm immediately before execution. " + spec.description, arguments)))
        elif spec.permission_level == 1:
            allowed = True
        elif spec.permission_level == 2 and settings.get("control.enabled", False):
            allowed = True
        elif grants and spec.permission_level is None:
            allowed = grants[0]["decision"] == "allow"
        else:
            allowed = bool(approve(PermissionRequest("execute", spec.name, spec.permission,
                                                    spec.description, arguments)))
        self.repository.audit("permission", f"{spec.name}: execution {'allowed' if allowed else 'denied'}")
        return allowed

    def disclose(self, spec: ToolSpec, arguments: dict, preview: str, approve: Approval) -> bool:
        allowed = bool(approve(PermissionRequest("disclose", spec.name, "cloud.disclose",
                            "Send this exact tool result to the selected AI provider for this response.", arguments, preview)))
        self.repository.audit("privacy", f"{spec.name}: cloud disclosure {'allowed' if allowed else 'denied'}")
        return allowed
