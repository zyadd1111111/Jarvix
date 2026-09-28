"""Real, bounded account APIs. Each operation is exposed through a permissioned tool.

GitHub accepts scoped personal access tokens. Discord accepts bot tokens only;
ordinary user-account automation is deliberately unsupported. OAuth tokens for
Google and Spotify are obtained with the native loopback flow in account_oauth.
"""
from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import quote

import httpx

from .account_oauth import PROVIDERS, token_request
from jarvix.runtime import check_cancelled

MAX_RESPONSE = 2 * 1024 * 1024


class AccountError(RuntimeError):
    def __init__(self, message, *, needs_auth=False):
        super().__init__(message)
        self.needs_auth = needs_auth


def segment(value):
    value = str(value)
    if not value or value in {".", ".."} or len(value) > 500 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid account resource identifier.")
    return quote(value, safe="")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Use a date/time with a time zone, for example 2026-09-28T09:00:00-04:00.") from None
    return value


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()
                if key.lower() not in {"access_token", "refresh_token", "client_secret", "authorization", "token"}}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


class AccountAdapter:
    integration_id = ""
    base_url = ""
    identity_path = ""

    def __init__(self, *, client=None, save_token=None, get_secret=None):
        self.client = client
        self.save_token = save_token
        self.get_secret = get_secret or (lambda: None)
        self.token = {}
        self.account = ""
        self.scopes = []
        self.last_activity = None
        self.lock = threading.RLock()

    def connect(self, credential):
        with self.lock:
            check_cancelled()
            if credential.startswith("{"):
                try:
                    data = json.loads(credential)
                    if not isinstance(data, dict):
                        raise ValueError
                    self.token = {key: data[key] for key in ("access_token", "refresh_token", "expires_at", "scope", "client_id") if key in data}
                except (TypeError, ValueError):
                    raise AccountError("Invalid account credential.") from None
            else:
                self.token = {"access_token": credential}
            if not isinstance(self.token.get("access_token"), str) or not self.token["access_token"]:
                raise AccountError("Missing account credential.")
            if len(self.token["access_token"]) > 8192 or any(ord(c) < 32 for c in self.token["access_token"]):
                raise AccountError("Invalid account credential.")
            profile = self.request("GET", self.identity_path, **self.identity_parameters())
            if not isinstance(profile, dict) or not self.valid_profile(profile):
                raise AccountError("The account identity could not be verified.")
            self.account = str(self.profile_name(profile))[:250]
            self.scopes = str(self.token.get("scope", "")).replace(",", " ").split()
            return True

    def identity_parameters(self):
        return {}

    def valid_profile(self, profile):
        return bool(profile.get("id"))

    def profile_name(self, profile):
        return profile.get("email") or profile.get("display_name") or profile.get("login") or profile.get("username") or profile.get("id")

    def disconnect(self):
        with self.lock:
            self.token.clear()
            self.account = ""
            self.scopes = []

    def _refresh(self):
        expiry = self.token.get("expires_at")
        if expiry is not None and not math.isfinite(float(expiry)):
            raise AccountError("Invalid account expiry. Reconnect in Integrations.", needs_auth=True)
        if expiry is None or time.time() < float(expiry) - 30:
            return
        provider = PROVIDERS.get(self.integration_id)
        if not provider or not self.token.get("refresh_token") or not self.token.get("client_id"):
            raise AccountError("The account session expired. Reconnect in Integrations.", needs_auth=True)
        data = {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                "client_id": self.token["client_id"]}
        secret = self.get_secret()
        if secret:
            data["client_secret"] = secret
        try:
            updated = token_request(provider, data, self.client)
            updated["expires_at"] = time.time() + float(updated.pop("expires_in", 3600))
            merged = {**self.token, **updated}
            if self.save_token:
                self.save_token(json.dumps(merged, separators=(",", ":")))
            self.token = merged
            self.scopes = str(self.token.get("scope", "")).replace(",", " ").split()
        except Exception:
            raise AccountError("The account session could not be renewed. Reconnect in Integrations.", needs_auth=True) from None

    def request(self, method, path, *, params=None, body=None, content=None, content_type=None,
                binary=False, maximum=MAX_RESPONSE):
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("Invalid account API route.")
        with self.lock:
            check_cancelled()
            self._refresh()
            token = self.token.get("access_token")
            if not token:
                raise AccountError("Not connected. Connect this account in Integrations.", needs_auth=True)
            prefix = "Bot" if self.integration_id == "discord" else "Bearer"
            headers = {"Authorization": f"{prefix} {token}", "Accept": "application/json"}
            if self.integration_id == "github":
                headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
            if content_type:
                headers["Content-Type"] = content_type
            owned = self.client is None
            client = self.client or httpx.Client(timeout=20, follow_redirects=False)
            try:
                kwargs = {"params": params, "headers": headers, "follow_redirects": False}
                if body is not None:
                    kwargs["json"] = body
                if content is not None:
                    kwargs["content"] = content
                with client.stream(method, self.base_url + path, **kwargs) as response:
                    if response.status_code == 401:
                        raise AccountError("Account authentication expired or was revoked. Reconnect in Integrations.", needs_auth=True)
                    if response.status_code == 403:
                        raise AccountError("The account lacks permission, or the provider restricts this operation.")
                    if response.status_code == 429:
                        raise AccountError("The account provider rate limit was reached. Retry later.")
                    if not 200 <= response.status_code < 300:
                        raise AccountError(f"The account request failed (HTTP {response.status_code}).")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        check_cancelled()
                        data.extend(chunk)
                        if len(data) > maximum:
                            raise AccountError("The account result is too large. Narrow the request.")
                    if self.integration_id == "github" and response.headers.get("X-OAuth-Scopes"):
                        self.token["scope"] = response.headers["X-OAuth-Scopes"]
                    self.last_activity = datetime.now().astimezone().isoformat()
                    if binary:
                        return bytes(data)
                    if not data:
                        return {"accepted": True}
                    return sanitize(json.loads(data))
            except AccountError:
                raise
            except InterruptedError:
                raise
            except Exception:
                raise AccountError("The account request could not complete. Check the connection and retry.") from None
            finally:
                if owned:
                    client.close()


class GitHubAdapter(AccountAdapter):
    integration_id = "github"
    base_url = "https://api.github.com"
    identity_path = "/user"

    @staticmethod
    def repo(owner, repo):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo):
            raise ValueError("Use a GitHub owner and repository name.")
        return f"/repos/{segment(owner)}/{segment(repo)}"

    def repositories(self, limit=30):
        return self.request("GET", "/user/repos", params={"per_page": limit, "sort": "updated"})

    def issues(self, owner, repo, state="open", limit=30):
        return self.request("GET", self.repo(owner, repo) + "/issues", params={"state": state, "per_page": limit})

    def pull_requests(self, owner, repo, state="open", limit=30):
        return self.request("GET", self.repo(owner, repo) + "/pulls", params={"state": state, "per_page": limit})

    def branches(self, owner, repo, limit=30):
        return self.request("GET", self.repo(owner, repo) + "/branches", params={"per_page": limit})

    def commits(self, owner, repo, limit=30):
        return self.request("GET", self.repo(owner, repo) + "/commits", params={"per_page": limit})

    def notifications(self, limit=30):
        return self.request("GET", "/notifications", params={"per_page": limit})

    def create_issue(self, owner, repo, title, body=""):
        return self.request("POST", self.repo(owner, repo) + "/issues", body={"title": title, "body": body})

    def update_issue(self, owner, repo, number, title, body, state="open"):
        return self.request("PATCH", self.repo(owner, repo) + f"/issues/{int(number)}",
                            body={"title": title, "body": body, "state": state})

    def comment(self, owner, repo, number, body):
        return self.request("POST", self.repo(owner, repo) + f"/issues/{int(number)}/comments", body={"body": body})

    def create_pull_request(self, owner, repo, title, head, base, body=""):
        return self.request("POST", self.repo(owner, repo) + "/pulls",
                            body={"title": title, "head": head, "base": base, "body": body, "draft": True})

    def update_pull_request(self, owner, repo, number, title, body, state="open"):
        return self.request("PATCH", self.repo(owner, repo) + f"/pulls/{int(number)}",
                            body={"title": title, "body": body, "state": state})

    def create_branch(self, owner, repo, branch, sha):
        if not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
            raise ValueError("A full commit SHA is required.")
        return self.request("POST", self.repo(owner, repo) + "/git/refs",
                            body={"ref": "refs/heads/" + branch, "sha": sha})


class GmailAdapter(AccountAdapter):
    integration_id = "gmail"
    base_url = "https://gmail.googleapis.com"
    identity_path = "/gmail/v1/users/me/profile"

    def valid_profile(self, profile):
        return bool(profile.get("emailAddress"))

    def profile_name(self, profile):
        return profile["emailAddress"]

    def search(self, query="in:inbox", limit=20):
        return self.request("GET", "/gmail/v1/users/me/messages", params={"q": query, "maxResults": limit})

    def read(self, message_id):
        data = self.request("GET", "/gmail/v1/users/me/messages/" + segment(message_id), params={"format": "full"})
        def decode(part):
            text = []
            raw = part.get("body", {}).get("data", "")
            if part.get("mimeType") == "text/plain" and raw:
                try:
                    text.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace"))
                except ValueError:
                    pass
            for child in part.get("parts", []):
                text.extend(decode(child))
            return text
        payload = data.get("payload", {})
        return {"id": data.get("id"), "threadId": data.get("threadId"), "snippet": data.get("snippet"),
                "labels": data.get("labelIds", []), "headers": payload.get("headers", []),
                "text": "\n".join(decode(payload))[:16000]}

    def inbox(self, limit=10):
        rows = self.search("in:inbox", limit).get("messages", [])
        return {"messages": [self.read(row["id"]) for row in rows[:limit]]}

    def labels(self):
        return self.request("GET", "/gmail/v1/users/me/labels")

    def modify_labels(self, message_id, add=None, remove=None):
        return self.request("POST", "/gmail/v1/users/me/messages/" + segment(message_id) + "/modify",
                            body={"addLabelIds": add or [], "removeLabelIds": remove or []})

    def archive(self, message_id):
        return self.modify_labels(message_id, remove=["INBOX"])

    @staticmethod
    def message(to, subject, text, reply_to_message_id=None):
        mail = EmailMessage()
        try:
            mail["To"], mail["Subject"] = to, subject
            if reply_to_message_id:
                mail["In-Reply-To"] = reply_to_message_id
                mail["References"] = reply_to_message_id
            mail.set_content(text)
        except ValueError:
            raise ValueError("Email headers must not contain newlines.") from None
        return {"raw": base64.urlsafe_b64encode(mail.as_bytes()).decode()}

    def draft(self, to, subject, text, thread_id=None, reply_to_message_id=None):
        message = self.message(to, subject, text, reply_to_message_id)
        if thread_id:
            message["threadId"] = thread_id
        return self.request("POST", "/gmail/v1/users/me/drafts", body={"message": message})

    def send(self, to, subject, text, thread_id=None, reply_to_message_id=None):
        message = self.message(to, subject, text, reply_to_message_id)
        if thread_id:
            message["threadId"] = thread_id
        return self.request("POST", "/gmail/v1/users/me/messages/send", body=message)


class CalendarAdapter(AccountAdapter):
    integration_id = "google_calendar"
    base_url = "https://www.googleapis.com"
    identity_path = "/calendar/v3/calendars/primary"

    def profile_name(self, profile):
        return profile["id"]

    @staticmethod
    def events_path(calendar_id):
        return "/calendar/v3/calendars/" + segment(calendar_id) + "/events"

    def events(self, start, end, query="", calendar_id="primary", limit=30):
        return self.request("GET", self.events_path(calendar_id), params={"timeMin": timestamp(start),
                            "timeMax": timestamp(end), "q": query, "singleEvents": "true",
                            "orderBy": "startTime", "maxResults": limit})

    def availability(self, start, end, calendar_id="primary"):
        return self.request("POST", "/calendar/v3/freeBusy", body={"timeMin": timestamp(start),
                            "timeMax": timestamp(end), "items": [{"id": calendar_id}]})

    def create(self, title, start, end, description="", calendar_id="primary"):
        return self.request("POST", self.events_path(calendar_id), body={"summary": title,
                            "description": description, "start": {"dateTime": timestamp(start)},
                            "end": {"dateTime": timestamp(end)}})

    def update(self, event_id, title, start, end, description="", calendar_id="primary"):
        return self.request("PATCH", self.events_path(calendar_id) + "/" + segment(event_id),
                            body={"summary": title, "description": description,
                                  "start": {"dateTime": timestamp(start)}, "end": {"dateTime": timestamp(end)}})

    def cancel(self, event_id, calendar_id="primary"):
        return self.request("DELETE", self.events_path(calendar_id) + "/" + segment(event_id))


class DriveAdapter(AccountAdapter):
    integration_id = "google_drive"
    base_url = "https://www.googleapis.com"
    identity_path = "/drive/v3/about"

    def identity_parameters(self):
        return {"params": {"fields": "user(displayName,emailAddress,permissionId)"}}

    def valid_profile(self, profile):
        return bool(profile.get("user", {}).get("permissionId"))

    def profile_name(self, profile):
        return profile["user"].get("emailAddress", profile["user"].get("displayName", "Google Drive"))

    def search(self, query="trashed = false", limit=30):
        return self.request("GET", "/drive/v3/files", params={"q": query, "pageSize": limit,
            "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,webViewLink)", "orderBy": "modifiedTime desc"})

    def metadata(self, file_id):
        return self.request("GET", "/drive/v3/files/" + segment(file_id), params={
            "fields": "id,name,mimeType,size,modifiedTime,webViewLink,description,capabilities(canDownload)"})

    def create_text(self, name, text, parent_id=None):
        return self.upload(name, text.encode("utf-8"), "text/plain", parent_id)

    def upload(self, name, data, mime_type="application/octet-stream", parent_id=None):
        import secrets
        boundary = "jarvix_" + secrets.token_hex(20)
        if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime_type):
            raise ValueError("Invalid upload content type.")
        metadata = {"name": name, "mimeType": mime_type}
        if parent_id:
            metadata["parents"] = [parent_id]
        body = ((f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                + json.dumps(metadata) + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n").encode()
                + data + f"\r\n--{boundary}--\r\n".encode())
        return self.request("POST", "/upload/drive/v3/files", params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                            content=body, content_type="multipart/related; boundary=" + boundary)

    def download(self, file_id):
        return self.request("GET", "/drive/v3/files/" + segment(file_id), params={"alt": "media"},
                            binary=True, maximum=10 * 1024 * 1024)

    def export_text(self, file_id):
        data = self.request("GET", "/drive/v3/files/" + segment(file_id) + "/export",
                            params={"mimeType": "text/plain"}, binary=True)
        return {"text": data.decode("utf-8", "replace")}


class SpotifyAdapter(AccountAdapter):
    integration_id = "spotify"
    base_url = "https://api.spotify.com"
    identity_path = "/v1/me"

    def playback(self):
        return self.request("GET", "/v1/me/player")

    def devices(self):
        return self.request("GET", "/v1/me/player/devices")

    def search(self, query, kind="track", limit=10):
        return self.request("GET", "/v1/search", params={"q": query, "type": kind, "limit": limit})

    def play(self, device_id=None):
        return self.request("PUT", "/v1/me/player/play", params={"device_id": device_id} if device_id else None)

    def pause(self, device_id=None):
        return self.request("PUT", "/v1/me/player/pause", params={"device_id": device_id} if device_id else None)

    def next(self, device_id=None):
        return self.request("POST", "/v1/me/player/next", params={"device_id": device_id} if device_id else None)

    def previous(self, device_id=None):
        return self.request("POST", "/v1/me/player/previous", params={"device_id": device_id} if device_id else None)

    def choose_device(self, device_id):
        return self.request("PUT", "/v1/me/player", body={"device_ids": [device_id], "play": False})


class DiscordAdapter(AccountAdapter):
    integration_id = "discord"
    base_url = "https://discord.com/api/v10"
    identity_path = "/users/@me"

    def valid_profile(self, profile):
        return bool(profile.get("id") and profile.get("bot") is True)

    def guilds(self, limit=30):
        return self.request("GET", "/users/@me/guilds", params={"limit": limit})

    def channels(self, guild_id):
        return self.request("GET", "/guilds/" + segment(guild_id) + "/channels")

    def messages(self, channel_id, limit=30):
        return self.request("GET", "/channels/" + segment(channel_id) + "/messages", params={"limit": limit})

    def send(self, channel_id, text):
        return self.request("POST", "/channels/" + segment(channel_id) + "/messages",
                            body={"content": text, "allowed_mentions": {"parse": []}})


ADAPTERS = (GitHubAdapter, GmailAdapter, CalendarAdapter, DriveAdapter, SpotifyAdapter, DiscordAdapter)

