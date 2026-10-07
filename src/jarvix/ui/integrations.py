"""Account connection UI. Credentials never enter the tool/chat argument history."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout,
    QGridLayout, QScrollArea, QWidget, QHeaderView, QTableWidgetItem,
)

from jarvix.capabilities.account_oauth import PROVIDERS
from jarvix.capabilities.integration import INTEGRATIONS

from .chat import PermissionDialog
from .operator import ServiceJob
from .pages import IntegrationsPage as ProviderPage
from .pages import Page
from .widgets import button, label, table
from .icons import icon


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
        controls.addWidget(button(("Reconnect " if row["status"] == "Connected" else "Connect ")
                                  + INTEGRATIONS[key], self.accept, "Primary"))
        layout.addLayout(controls)


class IntegrationsPage(ProviderPage):
    subtitle = "AI credentials and connected accounts."

    def __init__(self, window):
        self.connections = {}
        self.cards = {}
        Page.__init__(self, window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        layout.addWidget(label("AI provider credentials", "SectionTitle"))
        providers = QGridLayout()
        providers.setColumnStretch(2, 1)
        self.status_labels = {}
        self.keys = {}
        for index, (provider, name) in enumerate((("openai", "OpenAI"), ("gemini", "Gemini"))):
            providers.addWidget(label(name), index, 0)
            state = label("Not configured", "Muted")
            self.status_labels[provider] = state
            providers.addWidget(state, index, 1)
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText(name + " API key")
            key.setAccessibleName(name + " API key")
            self.keys[provider] = key
            providers.addWidget(key, index, 2)
            providers.addWidget(button("Save key", lambda provider=provider: self.save(provider)), index, 3)
            providers.addWidget(button("Remove key", lambda provider=provider: self.remove(provider), "Quiet"), index, 4)
        layout.addLayout(providers)
        layout.addWidget(label("Credentials stay in the OS vault. A saved key is verified when you make a provider request.", "Muted", True))
        layout.addWidget(label("Connected accounts", "SectionTitle"))
        self.account_status = table(["Service", "Status", "Account", "Permissions", "Last activity", "Actions"])
        self.account_status.setObjectName("IntegrationAccounts")
        self.account_status.setMinimumHeight(330)
        self.account_status.setRowCount(len(INTEGRATIONS))
        header = self.account_status.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((130, 130, 180, 170, 135)):
            self.account_status.setColumnWidth(column, width)
        self.account_status.setColumnWidth(5, 110)
        self.account_status.verticalHeader().setDefaultSectionSize(66)
        layout.addWidget(self.account_status, 1)
        layout.addWidget(label("Account data is fetched only when you request it. Sensitive account changes require confirmation.", "Muted", True))
        self.account_rows = {}
        for index, (key, name) in enumerate(INTEGRATIONS.items()):
            self.account_rows[key] = index
            service_item = QTableWidgetItem(name)
            service_item.setIcon(icon("integrations"))
            self.account_status.setItem(index, 0, service_item)
            state = label("Not connected", "Muted")
            state.setParent(self)
            state.hide()
            detail = label("No account connected", "Muted", True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            self.account_status.setCellWidget(index, 2, detail)
            controls = QWidget()
            actions = QHBoxLayout()
            actions.setContentsMargins(4, 4, 4, 4)
            actions.setSpacing(4)
            controls.setLayout(actions)
            connect = button("Connect", lambda key=key: self.configure(key))
            manage = button("Manage permissions", lambda key=key: self.configure(key), "Quiet")
            disconnect = button("Disconnect", lambda key=key: self.disconnect_account(key), "Quiet")
            cancel = button("Cancel connection", lambda key=key: self.cancel_connection(key), "Quiet")
            for action in (connect, manage, disconnect, cancel):
                action.setToolTip(action.text() + " " + name)
                action.setAccessibleName(action.text() + " " + name)
                actions.addWidget(action)
            self.account_status.setCellWidget(index, 5, controls)
            self.cards[key] = (state, detail, connect, manage, disconnect, cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self):
        statuses = self.services.provider_status()
        for provider, widget in self.status_labels.items():
            widget.setText("Key configured" if statuses.get(provider) else "Not configured")
        if not self.cards:
            return
        for row in self.services.integrations.status():
            state, detail, connect, manage, disconnect, cancel = self.cards[row["id"]]
            busy = row["id"] in self.connections
            status = "Connecting" if busy else row["status"]
            state.setText(status)
            index = self.account_rows[row["id"]]
            item = QTableWidgetItem(status)
            item.setToolTip(row.get("error") or status)
            self.account_status.setItem(index, 1, item)
            text = row["account"] or "No verified account"
            if row["error"]:
                text += "\n" + row["error"]
            detail.setText(text)
            permission = ("Read and write" if row["write_enabled"] else "Read only")
            permission_item = QTableWidgetItem(permission if row["account"] else "Not granted")
            permission_item.setToolTip(", ".join(row["scopes"]) or "Provider-managed permissions")
            self.account_status.setItem(index, 3, permission_item)
            self.account_status.setItem(index, 4, QTableWidgetItem(row["last_activity"] or "No activity"))
            self.account_status.item(index, 0).setToolTip(
                f"{row['name']}\nAccount: {row['account'] or 'No verified account'}\n"
                f"Permissions: {', '.join(row['scopes']) or 'Not granted'}\n"
                f"Last activity: {row['last_activity'] or 'No activity'}")
            connect.setVisible(not busy and row["status"] != "Connected")
            connect.setEnabled(not busy and row["status"] != "Connected")
            manage.setVisible(not busy and row["status"] != "Not connected")
            manage.setEnabled(not busy)
            disconnect.setVisible(not busy and row["status"] != "Not connected")
            disconnect.setEnabled(not busy)
            cancel.setVisible(busy)
        self.account_status.setColumnWidth(5, max(110, max(
            self.account_status.cellWidget(row, 5).sizeHint().width()
            for row in range(self.account_status.rowCount())) + 8))
        self.update_account_columns()

    def update_account_columns(self):
        width = self.account_status.viewport().width()
        compact = width < 820
        narrow = width < 680
        self.account_status.setColumnHidden(4, compact)
        self.account_status.setColumnHidden(3, compact)
        self.account_status.setColumnHidden(2, narrow)
        self.account_status.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch if narrow else QHeaderView.ResizeMode.Interactive)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "account_status"):
            self.update_account_columns()

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
