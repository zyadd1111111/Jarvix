# Jarvix

Jarvix is a local-first desktop AI assistant built with Python, PySide6 and SQLite. Version 0.5.0, **Intelligence**, adds cited document reading, recoverable cross-app plans and explicit scoped memory to the existing Nexus operator.

## What's new in 0.5.0

- **Local document intelligence:** safely extract and search PDF, DOCX, PPTX, XLSX, text, Markdown, JSON, CSV and source files. Follow source locations, section/table fragments and revision-checked continuation pages. Create project knowledge collections without copying or uploading documents.
- **Recoverable plans:** preview edited recovery plans after failures, keep completed results, skip failed dependencies and optionally finish independent reads. Uncertain writes require fresh inspection; checkpoints never authorize replaying them.
- **Cross-app workflows:** compose project/Git review, document-to-note/task, school workspace, calendar and inbox/channel briefings through existing permissioned tools. `intelligence.plan` creates a preview before execution.
- **Intentional memory:** duplicate review, explicit subject conflicts, project/workspace scopes, lexical relevance, editable provenance and a “Why is this remembered?” view. Temporary conversation context expires in memory and is cleared at shutdown.
- **Browser intelligence:** cited visible-page excerpts, tab search, value-free form structure, guarded duplicate cleanup and optional on-demand download inspection. Downloads require a separate extension permission.
- **Guarded rollback and Stop:** automation configuration undo uses fresh confirmation; receipt previews check later edits. Multi-action rollback reports partial progress. Queued browser/account calls and supported active HTTP body reads can be cancelled.

Local summaries and Q&A are extractive evidence, not generated semantic answers. Cloud synthesis remains available through Chat only after disclosure approval. Document parsing has explicit size/complexity bounds; encrypted, scanned-only and unsupported files report limitations. No spreadsheet formulas or embedded document code execute.

## What got revamped in 0.5

Files opens document/collection actions with citation views. Operator exposes recovery-plan editing and partial completion. Chat shows source cards. Ctrl+K includes conversations and integrations, with an explicit browser-tab lookup; it never polls browsing activity. Existing services, storage, permissions and the dark glass design remain in place.

## What's new in 0.4.0

- **Deep browser control**: Native messaging bridge with Chrome/Edge for tab management, page inspection, element targeting, form interaction, session save/restore and duplicate cleanup. Accessibility-first automation without screen coordinates.
- **Account integrations**: OAuth where supported and verified account-token connections for Gmail, Google Calendar, Google Drive, GitHub, Spotify and Discord. Discord requires permitted bot credentials. Credentials stay in the OS vault; cards remain "Not connected" until authentication succeeds.
- **Streaming responses**: Token-level streaming from OpenAI and Gemini with incremental UI rendering, clean cancellation and proper state persistence. No blocking or duplicate messages.
- **Multimodal chat**: Image and screenshot attachments for vision-capable models. Drag-drop images, paste from clipboard, capture active window or selected monitor. Local disclosure approval before provider submission.
- **Wake-word voice**: Optional local keyword spotting with sherpa-onnx. "Jarvix" activates hands-free mode with visible microphone indicator, sensitivity control and instant pause. Audio stays local; no cloud streaming.
- **Closed-app scheduling**: Windows Task Scheduler integration for approved workflows. Weekday/time schedules run even when Jarvix is closed. Signed task definitions, least privilege, interactive-user context and explicit per-workflow approval.
- **Recycle restoration**: Track Jarvix-recycled items with Shell receipts. List recent deletions, preview restore targets, check conflicts and restore with fingerprint verification. Activity log and original-path tracking.
- **Operator 2.0**: Improved session management with dependency-aware steps, structured retry policies, checkpoints, resumable sessions and partial-completion handling. Better failure reasons and recovery suggestions.
- **Context Engine 2.0**: Richer context snapshots with active app, browser tab, selected files, workspace, project and clipboard type. Explicit opt-in permission model with inspectable captured state.
- **Integration Center**: Proper Integrations UI with connection cards showing status, connected account, permissions, last activity and manage actions. Real OAuth state, no fake connection claims.

## What was in 0.3.0

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

For this local version, build or extract `dist\Jarvix-0.5.0-windows-x64.zip`, then run `Jarvix\Jarvix.exe`. You can also run `dist\Jarvix\Jarvix.exe` directly. Keep the extracted folder together, including `_internal`. This is an unsigned portable build.

Published downloads are available from [GitHub Releases](https://github.com/zyadd1111111/Jarvix/releases). A local build does not publish a release; use the version shown on that page, or build the current checkout with `scripts\build.ps1`.

For browser control, load the included `browser_extension` folder as an unpacked extension in Chrome/Edge. In Jarvix Settings, enable browser control; use Actions → `browser.install_bridge` to register that browser's extension ID and the included `JarvixBrowserHost.exe`. Then run `browser.connect` and press Connect in the extension popup. Its Downloads access is optional and removable. Source builds use `assets/browser_extension` and the same packaged helper.

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

**Ctrl/Cmd+K** searches pages, apps, files, tasks, notes, projects, conversations, routines, automations, integrations and tools. Connected browser tabs are fetched only on request. **Ctrl+N** starts a conversation; **Alt+Left/Right** navigates section history. Window geometry and the last section persist. Close-to-tray is optional.

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

Native operator control requires Windows. The Windows-start trigger means the first Jarvix run after boot; closed-app schedules require separate Task Scheduler opt-in. Stop prevents subsequent work and closes supported active HTTP responses; connection establishment remains bounded by transport timeout. Completed side effects are not automatically rolled back. Wake-word support requires the optional `sherpa-onnx` package and a local keyword-spotting model. Real account APIs, microphone recognition and connected-browser behavior require configured accounts/devices and are not verified by mocked tests. See [SECURITY.md](SECURITY.md) and [verification](docs/VERIFICATION.md).
