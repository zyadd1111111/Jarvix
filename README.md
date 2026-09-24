# Jarvix

Jarvix is a local-first desktop AI assistant built with Python, PySide6 and SQLite. Version 0.2.0 grows the original assistant into a permission-aware desktop operator with structured tools, local storage and a broader Windows-ready interface.

## What's new in 0.2.0

- 164 registered, schema-validated tools for files, apps, windows, system information, notes, tasks, projects, developer workflows and more.
- A guided Actions panel for running local tools, plus a command palette and quick navigation.
- Push-to-talk microphone input, response playback, a notification center and optional system-tray behavior.
- Workspaces, application discovery and aliases, file previews and summaries, clipboard history, screenshots, and multi-step automations.
- OpenAI and Gemini provider adapters, credential storage through the operating-system vault, and explicit context previews before local data is shared with a provider.
- A portable Windows build alongside the source installation.

## What got revamped

- The original small chat/action router has become a service-based application with an orchestrator, tool registry, provider adapters, background workers and reusable capability services.
- SQLite storage now covers conversations, notes, tasks, projects, memories, permissions, activity and automation metadata, with an additive migration from the earlier database.
- Computer and file operations use structured arguments and scoped roots; destructive actions require fresh confirmation, and screen, clipboard and microphone access begin disabled.
- The desktop app now has 13 sections for Home, Chat, Voice, Tasks, Memory, Notes, Files, Apps, Automations, Integrations, System, Activity and Settings.

## Install on Windows

Download and extract `Jarvix-0.2.0-windows-x64.zip` from the [latest GitHub release](https://github.com/zyadd1111111/Jarvix/releases/latest), then run `Jarvix\Jarvix.exe`. Keep the extracted folder together, including `_internal`. This is an unsigned portable build. You can also run the locally built `dist\Jarvix\Jarvix.exe`.

## Install from source

Requires Python 3.11 or newer. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m jarvix
```

On macOS or Linux, activate the virtual environment with `source .venv/bin/activate`, install with `python -m pip install -e ".[dev]"`, then run `python -m jarvix`. Local tools work without an AI key; configure an OpenAI or Gemini key in Settings to use cloud chat. `requirements-lock.txt` records the verified dependency set. `scripts\launch.ps1` starts Jarvix without a console on Windows.

Use `--data-dir .\artifacts\test-profile` for an isolated profile. Storage otherwise defaults to `%LOCALAPPDATA%\Jarvix\jarvix.db` on Windows or the platform's user data directory.

## First run

1. Add file roots under Settings before using file or developer tools. Protected paths, links and junctions are rejected.
2. Enable computer control only when needed. Clipboard, screen and microphone permissions start off.
3. Use **Actions** or **Ctrl+Shift+K** to inspect a tool's arguments and run it locally. Optional fields are omitted unless selected; complex arrays use JSON.
4. Configure an OpenAI or Gemini key in Settings. Keys use the OS vault, and environment keys take precedence. Local results are previewed before any are shared with a provider.
5. Select a microphone and hold to talk. Transcription uses an installed Windows speech language locally and does not submit messages automatically.

**Ctrl/Cmd+K** searches pages, apps, files, tasks, notes, projects, routines and tools. **Ctrl+N** starts a conversation; **Alt+Left/Right** navigates section history. Window geometry and the last section persist. Close-to-tray is optional.

## Capabilities

| Area | Available behavior |
| --- | --- |
| Computer | App discovery, aliases, favorites, groups; window focus, minimize, maximize, restore, close and foreground inspection |
| Files | Filtered, largest and recent listings; duplicates, summaries, create/copy/move/rename, batch previews, organization, undo, ZIP, recycling, text/JSON/CSV previews and PDF metadata |
| System | CPU/RAM/processes, storage, battery, adapters, uptime, devices, monitors, GPU/audio information, volume/media, confirmed termination and power operations |
| Clipboard and screen | Explicit read/write/capture/history/pinning/classification, note/task conversion, selected monitor/window screenshots and local history |
| Productivity | Task priorities/tags/projects/subtasks/recurrence/views; note folders/tags/pins/versions/export; memory categories/importance/expiry; project summaries |
| Developer | Language/Git inspection, safe status/diff/search, Python/Node/Lua initializers, confirmed argv commands, incremental output, cancellation and history |
| Workspaces and browser | Configured apps/folders/sites/notes/routines, bookmarks/site groups and explicitly imported history; browser actions open URLs without scraping |
| Automation | Manual multi-action routines, eleven trigger types, previews/history/templates; scheduled actions are limited to local summaries and notifications |
| Desktop and chat | Conversation search/pin/rename/delete/export, edit/regenerate branches, task/note conversion, Markdown, local tool timeline, context inspector, notification inbox and tray |

Gmail, Calendar, Drive, GitHub, Spotify and Discord have an adapter/credential boundary and show **Not connected** until configured. No account results are simulated.

## Safety and local data

Level 1 reads run immediately. Level 2 reversible actions require normal control or one-time approval. Level 3 always requires fresh confirmation; saved grants cannot bypass it. Configured app arguments require confirmation on every launch. Cloud disclosure is separate. Clipboard history is never monitored passively.

SQLite is not encrypted. Credentials have no plaintext fallback. Close Jarvix before manually copying its profile for backup. Tools call services; UI calls the application facade; providers never control the OS. The agent defaults to six model rounds, twelve model tool calls and thirty-two nested operations. Cancellation and deadlines are shared across nested actions.

## Verify and build

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build.ps1
```

Tests use isolated profiles, mocked provider HTTP and mocked destructive OS actions. Windows native read-only checks and packaged startup are verified separately. Live paid-provider requests and real microphone recognition need user hardware/accounts.

Wake words, visual AI/OCR, image attachments, token streaming, deep browser control, account actions, recycle restoration and scheduling while closed remain unimplemented. Screenshots are local captures; PDF support is metadata inspection. Stop prevents subsequent work, but an active HTTP read may wait for its timeout. Completed side effects are not automatically rolled back. See [SECURITY.md](SECURITY.md) and [the operator checkpoint](docs/OPERATOR-CHECKPOINT.md).
