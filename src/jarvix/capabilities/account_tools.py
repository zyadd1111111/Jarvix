"""Explicit account operations; no model-visible credentials or arbitrary HTTP API."""
from __future__ import annotations

import json
import mimetypes

from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled

from .schema import array, enum, integer, register, string

IDENTIFIER = string(500)
LIMIT = integer(1, 30)
REPO = {"owner": string(100), "repo": string(100)}
MAIL = {"to": string(500), "subject": string(500), "text": string(16000, 0),
        "thread_id": IDENTIFIER, "reply_to_message_id": string(500)}
EVENT = {"title": string(500), "start": string(80), "end": string(80),
         "description": string(8000, 0), "calendar_id": IDENTIFIER}


def bounded(value, depth=0):
    """Keep broad account listings useful within the existing tool result budget."""
    if depth > 12:
        return "[nested content omitted]"
    if isinstance(value, dict):
        return {key: bounded(item, depth + 1) for key, item in value.items()
                if not key.endswith("_url") or key in {"html_url", "avatar_url"}}
    if isinstance(value, list):
        return [bounded(item, depth + 1) for item in value[:30]]
    if isinstance(value, str) and len(value) > 16000:
        return value[:16000] + "\n[truncated]"
    return value


def register_accounts(s, registry):
    def add(name, description, props, required=(), *, level=1, write=False, method=None):
        integration, operation = name.split(".", 1)

        def handler(**arguments):
            result = s.integrations.invoke(integration, method or operation, arguments, write=write)
            if result.ok:
                result.data = bounded(result.data)
                if len(json.dumps(result.data, ensure_ascii=False)) > 56000:
                    return ToolResult(False, error="The account result is too large. Reduce limit or narrow the search.")
            return result

        register(registry, name, description, props, required, handler, level,
                 "integration." + ("write" if write else "read"), risk="external" if write else "read")

    add("github.repositories", "List repositories visible to your connected GitHub account.", {"limit": LIMIT})
    for operation, description in (("issues", "issues"), ("pull_requests", "pull requests")):
        add("github." + operation, f"Read repository {description} from GitHub.",
            {**REPO, "state": enum("open", "closed", "all"), "limit": LIMIT}, ("owner", "repo"))
    for operation in ("branches", "commits"):
        add("github." + operation, f"Read GitHub repository {operation}.", {**REPO, "limit": LIMIT}, ("owner", "repo"))
    add("github.notifications", "Read GitHub notifications allowed by your token.", {"limit": LIMIT})
    add("github.create_issue", "Create a GitHub issue after confirming its repository and exact contents.",
        {**REPO, "title": string(500), "body": string(16000, 0)}, ("owner", "repo", "title"), level=3, write=True)
    for operation in ("update_issue", "update_pull_request"):
        add("github." + operation, "Update this GitHub issue or pull request after fresh confirmation.",
            {**REPO, "number": integer(1, 100000000), "title": string(500), "body": string(16000, 0),
             "state": enum("open", "closed")}, ("owner", "repo", "number", "title", "body"), level=3, write=True)
    add("github.comment", "Post this exact comment to a GitHub issue or pull request after confirmation.",
        {**REPO, "number": integer(1, 100000000), "body": string(16000)},
        ("owner", "repo", "number", "body"), level=3, write=True)
    add("github.create_pull_request", "Create a draft pull request from existing GitHub branches after confirmation.",
        {**REPO, "title": string(500), "head": string(250), "base": string(250), "body": string(16000, 0)},
        ("owner", "repo", "title", "head", "base"), level=3, write=True)
    add("github.create_branch", "Create a GitHub branch at an existing full commit SHA after confirmation.",
        {**REPO, "branch": string(250), "sha": {"type": "string", "pattern": "^[a-fA-F0-9]{40}$"}},
        ("owner", "repo", "branch", "sha"), level=3, write=True)

    add("gmail.search", "Search Gmail with a Gmail query; returns IDs for gmail.read.",
        {"query": string(1000, 0), "limit": LIMIT})
    add("gmail.read", "Read an explicitly selected Gmail message; cloud disclosure is separately confirmed.",
        {"message_id": IDENTIFIER}, ("message_id",))
    add("gmail.inbox", "Read a small inbox batch for summarization after disclosure approval.", {"limit": integer(1, 5)})
    add("gmail.labels", "List Gmail labels.", {})
    add("gmail.modify_labels", "Change Gmail message labels after confirming the exact change.",
        {"message_id": IDENTIFIER, "add": array(IDENTIFIER, 30), "remove": array(IDENTIFIER, 30)},
        ("message_id",), level=3, write=True)
    add("gmail.archive", "Archive the selected Gmail message after confirmation.",
        {"message_id": IDENTIFIER}, ("message_id",), level=3, write=True)
    add("gmail.draft", "Save this email/reply as a Gmail draft; does not send it.", MAIL,
        ("to", "subject", "text"), level=3, write=True)
    add("gmail.send", "SEND EMAIL. Confirm the recipients, subject and exact message immediately before sending.",
        MAIL, ("to", "subject", "text"), level=3, write=True)

    add("google_calendar.events", "Read or search calendar events in an explicit time interval.",
        {"start": string(80), "end": string(80), "query": string(1000, 0),
         "calendar_id": IDENTIFIER, "limit": LIMIT}, ("start", "end"))
    add("google_calendar.availability", "Read calendar free/busy availability without changing events.",
        {"start": string(80), "end": string(80), "calendar_id": IDENTIFIER}, ("start", "end"))
    add("google_calendar.create", "Create this calendar event after confirmation.", EVENT,
        ("title", "start", "end"), level=3, write=True)
    add("google_calendar.update", "Update this calendar event after confirmation.", {**EVENT, "event_id": IDENTIFIER},
        ("event_id", "title", "start", "end"), level=3, write=True)
    add("google_calendar.cancel", "Cancel the selected calendar event after fresh confirmation.",
        {"event_id": IDENTIFIER, "calendar_id": IDENTIFIER}, ("event_id",), level=3, write=True)

    add("google_drive.search", "Search Drive using its query syntax; returned links can be opened with web.open.",
        {"query": string(1000), "limit": LIMIT})
    add("google_drive.metadata", "Inspect a Google Drive file's metadata and document link.",
        {"file_id": IDENTIFIER}, ("file_id",))
    add("google_drive.export_text", "Read a Google document through its supported plain-text export.",
        {"file_id": IDENTIFIER}, ("file_id",))
    add("google_drive.create_text", "Upload this exact text as a new Drive file after confirmation.",
        {"name": string(250), "text": string(16000, 0), "parent_id": IDENTIFIER},
        ("name", "text"), level=3, write=True)

    def upload(path, parent_id=None):
        source = s.files.path(path)
        if not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Choose a regular file up to 10 MB in an allowed root.")
        with source.open("rb") as stream:
            data = stream.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("Upload exceeds 10 MB.")
        check_cancelled()
        return s.integrations.invoke("google_drive", "upload", {"name": source.name, "data": data,
            "mime_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream", "parent_id": parent_id}, write=True)

    def download(file_id, destination):
        target = s.files.path(destination, existing=False, mutate=True)
        if target.exists() or not target.parent.is_dir():
            raise ValueError("Choose a new file destination inside an allowed root.")
        result = s.integrations.invoke("google_drive", "download", {"file_id": file_id})
        if not result.ok:
            return result
        check_cancelled()
        s.files.path(target, existing=False, mutate=True)
        with target.open("xb") as output:
            output.write(result.data)
        return {"path": str(target), "bytes": len(result.data), "downloaded": True}

    register(registry, "google_drive.upload", "Upload this local file (up to 10 MB) to Drive after fresh disclosure confirmation.",
        {"path": string(4096), "parent_id": IDENTIFIER}, ("path",), upload, 3, "integration.write", "external")
    register(registry, "google_drive.download", "Download a Drive file up to 10 MB to a new allowed local path; never overwrite.",
        {"file_id": IDENTIFIER, "destination": string(4096)}, ("file_id", "destination"), download, 2, "files.write")

    add("spotify.playback", "Read current Spotify playback; an empty result means no active playback.", {})
    add("spotify.devices", "List Spotify playback devices available to your account.", {})
    add("spotify.search", "Search Spotify tracks, artists, albums or playlists.",
        {"query": string(1000), "kind": enum("track", "artist", "album", "playlist"), "limit": integer(1, 10)}, ("query",))
    for operation in ("play", "pause", "next", "previous"):
        add("spotify." + operation, f"Control Spotify playback: {operation}. Requires a permitted account/device.",
            {"device_id": IDENTIFIER}, level=2, write=True)
    add("spotify.choose_device", "Transfer Spotify playback to a known available device, without starting playback.",
        {"device_id": IDENTIFIER}, ("device_id",), level=2, write=True)

    add("discord.guilds", "Read servers visible to the connected Discord bot.", {"limit": LIMIT})
    add("discord.channels", "Read channels visible to the bot in a selected server.",
        {"guild_id": IDENTIFIER}, ("guild_id",))
    add("discord.messages", "Read messages allowed by the Discord bot's channel permissions and message-content access.",
        {"channel_id": IDENTIFIER, "limit": LIMIT}, ("channel_id",))
    add("discord.send", "POST MESSAGE as the connected Discord bot; confirm the channel and exact text. Mentions are disabled.",
        {"channel_id": IDENTIFIER, "text": string(2000)}, ("channel_id", "text"), level=3, write=True)

