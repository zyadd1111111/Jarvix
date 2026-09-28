"""Account connection UI. Credentials never enter the tool/chat argument history."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout

from jarvix.capabilities.account_oauth import PROVIDERS
from jarvix.capabilities.integration import INTEGRATIONS

from .chat import PermissionDialog
from .operator import ServiceJob
from .pages import IntegrationsPage as ProviderPage
from .widgets import button, label, panel


SETUP = {
    "gmail": "Create a Desktop app OAuth client in Google Cloud and enable the Gmail API.",
    "google_calendar": "Create a Desktop app OAuth client in Google Cloud and enable the Calendar API.",
    "google_drive": "Create a Desktop app OAuth client in Google Cloud and enable the Drive API.",
    "spotify": "Create a Spotify developer app with redirect URI http://127.0.0.1:8766/callback. Playback may require Premium and an active device.",
    "github": "Use a fine-grained GitHub token limited to selected repositories. Grant metadata, contents, issues and pull requests as needed. Notifications may require a classic token with notifications scope.",
    "discord": "Use a Discord application BOT token. The bot must be invited to the server with channel permissions; message content may require the Message Content intent. Personal account tokens are unsupported.",
}


class ConnectionDialog(QDialog):
    def __init__(self, service, key, parent=None):
        super().__init__(parent)
        self.setWindowTitle(INTEGRATIONS[key] + " · Account permissions")
        self.resize(570, 400)
        self.key = key
        layout = QVBoxLayout(self)
        layout.addWidget(label(INTEGRATIONS[key], "Heading"))
        layout.addWidget(label(SETUP[key], "Muted", True))
        row = next(item for item in service.status() if item["id"] == key)
        layout.addWidget(label("Verified permissions: " + (", ".join(row["scopes"]) or
            "Provider-managed token permissions; actual API access is checked by the provider."), "Muted", True))
        form = QFormLayout()
        self.client_id = QLineEdit(service.s.settings.get("integration." + key + ".client_id", ""))
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setMaxLength(8192)
        self.secret.setPlaceholderText("Stored only in the OS credential vault")
        if key in PROVIDERS:
            form.addRow("Desktop client ID", self.client_id)
            if key != "spotify":
                form.addRow("Client secret (if supplied)", self.secret)
            provider = PROVIDERS[key]
            layout.addWidget(label("Read access: " + ", ".join(provider.read_scopes), "Muted", True))
        else:
            form.addRow("Bot token" if key == "discord" else "Access token", self.secret)
            self.secret.setPlaceholderText("Leave blank to verify the saved credential")
        layout.addLayout(form)
        self.write = QCheckBox("Allow account changes (sensitive actions still require confirmation)")
        self.write.setChecked(False)
        layout.addWidget(self.write)
        if key in PROVIDERS:
            layout.addWidget(label("Additional write scopes: " + ", ".join(PROVIDERS[key].write_scopes), "Muted", True))
        layout.addWidget(label("Connect verifies your account with the provider. Reconnect replaces local permissions. Disconnect removes Jarvix's local credentials; revoke remote grants in the provider's account settings when needed.", "Muted", True))
        controls = QHBoxLayout()
        controls.addStretch()
        controls.addWidget(button("Cancel", self.reject, "Quiet"))
        controls.addWidget(button("Reconnect" if row["status"] == "Connected" else "Connect", self.accept, "Primary"))
        layout.addLayout(controls)


class IntegrationsPage(ProviderPage):
    subtitle = "Verified accounts, explicit permissions and credentials in your OS vault."

    def __init__(self, window):
        self.connections = {}
        self.cards = {}
        super().__init__(window)
        frame = self.account_status.parentWidget()
        layout = frame.layout()
        self.account_status.setMinimumHeight(200)
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if hasattr(widget, "text") and "Account adapters remain" in widget.text():
                widget.setText("Connect an account explicitly below. Jarvix never fetches account data during startup.")
        for key, name in INTEGRATIONS.items():
            card, body = panel()
            row = QHBoxLayout()
            row.addWidget(label(name, "Heading"))
            row.addStretch()
            state = label("Not connected", "Muted")
            row.addWidget(state)
            body.addLayout(row)
            detail = label("No account connected", "Muted", True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            body.addWidget(detail)
            actions = QHBoxLayout()
            connect = button("Connect", lambda key=key: self.configure(key), "Primary")
            manage = button("Manage permissions", lambda key=key: self.configure(key), "Quiet")
            disconnect = button("Disconnect", lambda key=key: self.disconnect_account(key), "Quiet")
            cancel = button("Cancel connection", lambda key=key: self.cancel_connection(key), "Quiet")
            for action in (connect, manage, disconnect, cancel):
                actions.addWidget(action)
            actions.addStretch()
            body.addLayout(actions)
            layout.addWidget(card)
            self.cards[key] = (state, detail, connect, manage, disconnect, cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self):
        super().refresh()
        if not self.cards:
            return
        for row in self.services.integrations.status():
            state, detail, connect, manage, disconnect, cancel = self.cards[row["id"]]
            busy = row["id"] in self.connections
            state.setText(row["status"])
            text = row["account"] or "No verified account"
            text += " · " + ("Writes enabled" if row["write_enabled"] else "Read access only")
            if row["last_activity"]:
                text += "\nLast account activity: " + row["last_activity"]
            if row["error"]:
                text += "\n" + row["error"]
            detail.setText(text)
            connect.setEnabled(not busy and row["status"] != "Connected")
            manage.setEnabled(not busy)
            disconnect.setEnabled(not busy)
            cancel.setVisible(busy)

    def configure(self, key):
        if key in self.connections:
            return
        dialog = ConnectionDialog(self.services.integrations, key, self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        client_id, credential, write = dialog.client_id.text().strip(), dialog.secret.text().strip(), dialog.write.isChecked()
        dialog.secret.clear()
        if key in PROVIDERS and not client_id:
            self.window.notify("Enter the provider's desktop client ID.")
            return

        def work(cancel, **_):
            accounts = self.services.integrations
            old = next(row for row in accounts.status() if row["id"] == key)
            secret = credential
            if key in PROVIDERS:
                secret = secret or accounts.vault.get(key + ".client_secret") or ""
            else:
                secret = secret or accounts.vault.get(key)
            if old["status"] != "Not connected":
                accounts.disconnect(key)
            if cancel.is_set():
                return {"cancelled": True}
            if key in PROVIDERS:
                result = accounts.connect_oauth(key, client_id, client_secret=secret, write=write, cancel=cancel)
                self.services.settings.set("integration." + key + ".client_id", client_id)
                return result
            return accounts.connect(key, secret, write=write)

        self.start(key, work)

    def disconnect_account(self, key):
        self.start(key, lambda **kwargs: self.services.execute_tool("integrations.disconnect", {"integration_id": key}, **kwargs))

    def start(self, key, work):
        if key in self.connections or self.window.closing:
            return
        worker = ServiceJob(work, self.window)
        self.connections[key] = worker
        self.window.jobs.add(worker)
        worker.approval.connect(self.request_approval)
        worker.failed.connect(lambda message: self.window.notify(message) if not self.window.closing else None)
        worker.succeeded.connect(self.completed)

        def finished():
            self.connections.pop(key, None)
            self.window.release_job(worker)
            if not self.window.closing:
                self.refresh()
        worker.finished.connect(finished)
        worker.start()
        self.refresh()

    def completed(self, result):
        if self.window.closing:
            return
        if hasattr(result, "ok") and not result.ok:
            self.window.notify(result.error)
        self.refresh()

    def request_approval(self, bridge):
        if self.window.closing:
            bridge.ready.set()
            return
        bridge.answer = PermissionDialog(bridge.request, self.window).exec() == QDialog.DialogCode.Accepted
        bridge.ready.set()

    def cancel_connection(self, key):
        if key in self.connections:
            self.connections[key].cancel.set()
        self.services.integrations.cancel_connection(key)

    def cancel_connections(self):
        self.timer.stop()
        for key in list(self.connections):
            self.cancel_connection(key)

