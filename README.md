# Jarvix

Jarvix is a local-first desktop AI assistant built with Python, PySide6 and SQLite. Version 0.3.0, **Operator**, connects the existing tools through visible plans, Windows accessibility control and local workflows.

## What's new in 0.3.0

- Windows UI Automation: inspect controls, target known elements, type, invoke, navigate, wait for controls and verify state. A visible control indicator provides pause and cancel.
- Operator plans with action previews, result references, verification, bounded read retries, cancellation and persistent session timelines. Uncertain mutations require inspection and a new plan before replay.
- Workflows with schedules, event triggers, conditions, branches, delays, error handling, explicit background grants and a vertical drag/reorder builder. Reusable manual routines share the engine.
- Explicit active-window/monitor capture, local Windows OCR, accessible text/error inspection and before/after image comparison.
- Optional global command overlay, inspectable context snapshots, workspace window layouts, project/terminal launch and guarded undo for supported local changes.
- An owned background runtime for workflows, reminders and notifications while running, including when minimized to the tray.

## What got revamped

- Home now surfaces operator progress, workspaces, routines, automation status and notifications alongside existing local data.
- Automations now have editable workflow blocks and live run controls. Chat action cards expose supported open, view and undo actions.
- Existing services, thirteen sections, OpenAI/Gemini adapters, SQLite storage and structured tools remain the foundation. Plans and workflows use the same per-tool permission boundary.
- Window capture isolates the selected application rather than copying an overlapping desktop rectangle; unsupported captures fail explicitly.

## Install on Windows

For this local version, build or extract `dist\Jarvix-0.3.0-windows-x64.zip`, then run `Jarvix\Jarvix.exe`. You can also run `dist\Jarvix\Jarvix.exe` directly. Keep the extracted folder together, including `_internal`. This is an unsigned portable build.

Published versions remain available on [GitHub Releases](https://github.com/zyadd1111111/Jarvix/releases); the local 0.3 checkpoint has not been published there.

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
6. Open **Operator** to inspect plans and session history. In **Automations**, build and test a workflow, then review its exact actions before enabling it. Enable the overlay and explicit context snapshots separately in Settings.

**Ctrl/Cmd+K** searches pages, apps, files, tasks, notes, projects, routines and tools. **Ctrl+N** starts a conversation; **Alt+Left/Right** navigates section history. Window geometry and the last section persist. Close-to-tray is optional.

## Capabilities

| Area | Available behavior |
| --- | --- |
| Computer | App discovery, aliases, favorites, groups; native window controls, accessibility trees, known-control targeting, waits, verification and Bluetooth/other Settings shortcuts |
| Files | Filtered, largest and recent listings; duplicates, summaries, create/copy/move/rename, batch previews, organization, undo, ZIP, recycling, text/JSON/CSV previews and PDF metadata |
| System | CPU/RAM/processes, storage, battery, adapters, uptime, devices, monitors, GPU/audio information, volume/media, confirmed termination and power operations |
| Clipboard and screen | Explicit clipboard actions/history, selected monitor/window screenshots, local OCR, accessible controls/text, error hints and image comparison |
| Productivity | Task priorities/tags/projects/subtasks/recurrence/views; note folders/tags/pins/versions/export; memory categories/importance/expiry; project summaries |
| Developer | Language/Git inspection, safe status/diff/search, Python/Node/Lua initializers, confirmed argv commands, incremental output, cancellation and history |
| Workspaces and browser | Configured apps/folders/sites/notes/routines, project and terminal directories, registered-app window layouts, owned-window closure, bookmarks/site groups and explicitly imported history |
| Automation | Manual routines, weekday/time/interval and local event triggers, conditions, branches, delays, safe read retries, run history, import/export and exact approved background actions |
| Desktop and chat | Operator timeline, workflow builder, optional overlay, conversation management, Markdown, tool cards, context inspector, notification inbox and tray |

Gmail, Calendar, Drive, GitHub, Spotify and Discord have an adapter/credential boundary and show **Not connected** until configured. No account results are simulated.

## Safety and local data

Level 1 reads run immediately, subject to screen/clipboard access switches. Level 2 reversible actions require normal control or one-time approval. Level 3 always requires fresh confirmation; saved grants cannot bypass it. Configured app arguments require confirmation on every launch. Cloud disclosure is separate. Clipboard history is never monitored passively; the optional clipboard-change trigger requires separate consent and retains only a hash.

UI actions revalidate the inspected window and control identity. Password/credential fields and confirmation bypasses are blocked. Workflows cannot grant themselves access: background writes need approval for the exact safe action arguments and still obey control/root permissions. Scheduled workspace setups must expand into approved app/folder/URL actions; UI input, arbitrary commands and mutable whole-workspace launches are not allowed unattended.

SQLite is not encrypted. Credentials have no plaintext fallback. Close Jarvix before manually copying its profile for backup. Tools call services; UI calls the application facade; providers never control the OS. The agent defaults to six model rounds, twelve model tool calls and thirty-two nested operations. Cancellation and deadlines are shared across nested actions.

## Verify and build

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build.ps1
```

Tests use isolated profiles, mocked provider HTTP and mocked destructive OS actions. Windows native read-only checks and packaged startup are verified separately. Live paid-provider requests and real microphone recognition need user hardware/accounts.

Run `.\.venv\Scripts\python.exe scripts\verify_operator_native.py` for an opt-in Windows UIA/capture/OCR smoke test that creates and controls only its own test windows. Local OCR needs an installed Windows recognition language; accessibility/capture support varies by application.

Wake words, cloud image attachments, token streaming, deep browser control, account actions, recycle restoration and scheduling while closed remain unimplemented. Native operator control requires Windows. The Windows-start trigger means the first Jarvix run after boot; it does not install a startup task. PDF support is metadata inspection. Stop prevents subsequent work, but an active HTTP read may wait for its timeout. Completed side effects are not automatically rolled back. See [SECURITY.md](SECURITY.md) and [the operator checkpoint](docs/OPERATOR-CHECKPOINT.md).
