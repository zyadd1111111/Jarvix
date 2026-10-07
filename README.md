# Jarvix

Jarvix is a local-first desktop AI assistant built with Python, PySide6 and SQLite. Version 0.9.0 rebuilds the desktop interface around compact workspaces while preserving the existing services, local data and permission boundaries.

## What's new in 0.9.0

**Two interfaces:** the existing interface is now **Legacy**. **Nexus** is a separate warm-cream desktop with cached frosted materials, a liquid command surface, document-style Chat and focused execution/research panes. Choose **Settings → Appearance → Interface**; the choice applies on the next launch so active work stays open. Both interfaces use the same local profile, services, tools and permissions. Legacy remains the default.

Nexus Appearance controls adjust glass strength, transparency and reduced motion. Local Qt materials work without native blur; supported Windows versions can add [DWM Mica/Acrylic backdrops](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type). The material renderer samples only its own desktop canvas, never the screen or other applications.

- A neutral technical theme, Segoe UI Variable typography, consistent SVG controls and grouped, collapsible navigation.
- Operator, Missions, Knowledge and Skills open in the main workspace. Projects has its own searchable view using the existing project tools.
- Home puts commands, current work, today's tasks, model status and recent resources in deliberate sections.
- Chat uses document-style messages, Markdown tables, compact tool activity and expandable details. Attachments, streaming, cancellation and disclosure approvals retain their existing behavior.

## What got revamped in 0.9

Files uses sortable rows, folder scope and native context menus. System uses compact telemetry and a process table. Integrations shows account status, permissions and connection actions in rows. Settings groups controls by category; workflows, Skills, dialogs and the command overlay share the same restrained controls. The backend and database schema are unchanged.

## What's new in 0.8.0

- **Personal context profiles:** name and pin existing memories, projects, workspaces, Missions and conversations. References persist locally; activation rechecks access, memory scope and expiry. Temporary conversation text remains temporary.
- **Cross-session checkpoints:** save a bounded progress summary, linked work and suggested next action. Review current sources after restart, reuse project observation plans and inspect retained Operator recovery information. Missing or revoked sources cannot supply stale summaries.
- **Reviewed Skills:** preview literal recipes from verified Operator work, manual routines or an explicit structured proposal. Edit, duplicate, test, enable/disable and export/import them through existing workflow permissions. Repeated verified work can suggest a routine after an explicit local scan; it never saves itself. Learned patterns exclude terminal commands; explicitly proposed commands retain their original fresh confirmation.
- **Safe continuation:** explicitly review bounded background observations through the existing supervisor, with deadlines, cancellation and protected session recovery. Only the existing safe metadata reads run unattended; uncertain writes are preserved for inspection, never replayed.
- **Session and Mission context:** temporary structured goals, files, decisions and unfinished steps expire unless explicitly promoted. Missions support milestones, deadlines, linked Skills and calculated progress. Graph links retain source, relationship type, confidence and scope.
- **Measured routing:** Local Only, Fast, Balanced, Best Quality and Custom profiles reuse advertised capabilities and saved latency/failure observations. Evaluation separates verified completion, partial work, retries, cancellation and failures; cloud fallback still needs disclosure approval.
- **Optional daily brief:** combine selected local tasks, Missions and workflow failures. Connected calendar, GitHub and email reads require explicit source/account configuration and a foreground request. Briefs never execute suggestions or upload local context.
- **Local maintenance:** inspect diagnostics, create checksum-verified SQLite snapshots, prepare an approved backup schedule and stage restores as isolated profiles. Check a configured public release and verify a downloaded artifact without installing it. A developer release checklist reuses Git, version, documentation and owned command results.
- **Device foundation and notifications:** explicitly paired vault-backed identities authenticate encrypted, expiring offline status/task/note envelopes with replay protection. No remote-control listener or command execution is exposed. Local notifications support grouping, snooze, muted categories and related work.

## What got revamped in 0.8

Home and Ctrl+K connect saved work, Skills, the optional brief, diagnostics and local maintenance. Operator can save selected progress or preview a learned recipe; the action dialog carries the reviewed recipe into its save form and exact confirmation. Saved context does not automatically activate, collect activity or enter AI prompts. Background observations complete only their reviewed reads; they do not resolve uncertain earlier actions or execute a saved next action.

Skill inputs/outputs describe literal recipes; they are not runtime parameter binding. Device transport and companion apps are not implemented. Restores disable unattended work and stored approvals for review; DPAPI-protected data requires the original Windows account. Update installation remains manual; a user-supplied checksum verifies integrity, not publisher identity. Unknown model quality remains unknown.

## What's new in 0.7.0

- **Project continuation:** resolve a selected project or linked Mission, inspect Git/project context and preview a small observation plan. Structured read handoffs share bounded results between specialist roles without copying transcripts or starting unnecessary agents.
- **Persistent Missions:** link tasks, notes, files, knowledge, conversations and Operator sessions; track progress and blockers, pause/resume, archive or cancel. Resume restores references without replaying uncertain actions.
- **Context Graph:** explicit local relationships between projects and cross-app references, with current access checks. External references stay unverified until inspected through their connected adapter.
- **Adaptive routing:** Coding and Document Analysis roles, compatible fallback chains, observed latency/failure history and cooldowns. Local fallbacks are tried first; any cloud fallback requires fresh disclosure approval. Partial streamed replies are preserved without retries.
- **Optional suggestions:** unfinished saved sessions, failed workflows and due tasks, with reasons, persistent dismissal and muted categories. Off by default; reads saved local metadata and never performs actions or uploads context.
- **App intelligence:** original tools power adapter dispatch, VS Code file/workspace hints and Git context, Explorer file operations, browser selections/downloads and Jarvix-owned terminal history. Unsupported app internals remain explicit limitations.

## What got revamped in 0.7

The command center and Ctrl+K connect Missions, continuation and suggestions. Chat shows the selected model and fallback; the existing Knowledge dialog includes mission and suggestion management. Nested workflow cancellation now exits inherited pause waits. The thirteen sections, storage, permission boundaries and installation methods remain in place.

## What's new in 0.6.0

- **Local AI and routing:** Ollama and OpenAI-compatible loopback endpoints, model discovery, health checks, task-role models and a local-only switch. Automatic routing checks advertised capabilities and context capacity; unknown models gain no assumed tool or vision support.
- **Local knowledge:** incremental keyword/embedding indexes for permitted documents, code, notes, memories, conversations, tasks and projects. Knowledge Spaces manage cited sources and explicit refresh; removed or changed sources cannot supply stale evidence. Embeddings use a configured local model; offline search falls back to keyword ranking.
- **Supervised work:** bounded long-running sessions, progress, dependency graphs, step deadlines, failure classification, cancellation and safe foreground/background handoff. Restart recovery preserves verified work and never blindly replays uncertain changes.
- **Extensions:** a versioned, reviewed declarative SDK for host-tool aliases, service/integration contributions, panels, palette commands and workflow presets. Changed manifests invalidate approval. Executable third-party plugin code is not loaded. See [extension guide](docs/EXTENSIONS.md).
- **Richer workflows:** structured variables and output references, bounded read-only loops, pinned subflows, templates and debug mode that previews writes.
- **App and Windows support:** guarded VS Code navigation, Explorer folder/selection inspection, native Windows Search filtered to allowed roots, virtual desktop awareness, open-with helpers, opt-in Recent items and confirmed Jarvix-only sign-in startup.
- **Optional data protection:** migrate sensitive fields to current-user Windows DPAPI, including memory, conversations, account metadata, selected notes and cached records. The SQLite file itself, other metadata and pre-existing backups are not encrypted.

## What got revamped in 0.6

Home and Operator show clearer progress and dependencies. Knowledge, Search, local model health and extension management are available from the command center and Ctrl+K. Settings expose routing/privacy controls; workflow editing preserves variables and result references. Existing account connection cards and optional wake controls are now wired into the shell. Existing thirteen sections, permission checks and installation paths remain.

Knowledge summaries/Q&A remain cited extractive evidence. Selected Drive sources contain explicitly imported metadata, not automatic cloud document downloads. Live account, model and native application behavior requires the user's configured environment.
Replacing embedding weights under the same model tag requires a forced index refresh. Routing prices are user-supplied; unknown prices remain unknown. A loopback endpoint must itself be configured to run locally if you require offline operation.

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

For this local version, build or extract `dist\Jarvix-0.9.0-windows-x64.zip`, then run `Jarvix\Jarvix.exe`. You can also run `dist\Jarvix\Jarvix.exe` directly. Keep the extracted folder together, including `_internal`. This is an unsigned portable build.

Published downloads are available from [GitHub Releases](https://github.com/zyadd1111111/Jarvix/releases). A local build does not publish a release; use the version shown on that page, or build the current checkout with `scripts\build.ps1`.

For browser control, load the included `browser_extension` folder as an unpacked extension in Chrome/Edge. In Jarvix Settings, enable browser control; use Tools → `browser.install_bridge` to register that browser's extension ID and the included `JarvixBrowserHost.exe`. Then run `browser.connect` and press Connect in the extension popup. Its Downloads access is optional and removable. Source builds use `assets/browser_extension` and the same packaged helper.

## Install from source

Requires Python 3.11 or newer. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m jarvix
```

On macOS or Linux, activate the virtual environment with `source .venv/bin/activate`, install with `python -m pip install -e ".[dev]"`, then run `python -m jarvix`. Local tools work without an AI key; configure an OpenAI or Gemini key in Settings to use cloud chat. `requirements-lock.txt` records the verified dependency set. `scripts\launch.ps1` starts Jarvix without a console on Windows.

Optional wake-word detection in source installs requires `python -m pip install -e ".[voice]"` and a compatible local keyword model. It stays off at startup. The default portable package does not bundle that optional engine or download models.

Use `--data-dir .\artifacts\test-profile` for an isolated profile. Storage otherwise defaults to `%LOCALAPPDATA%\Jarvix\jarvix.db` on Windows or the platform's user data directory.

## First run

1. Add file roots under Settings before using file or developer tools. Protected paths, links and junctions are rejected.
2. Enable computer control only when needed. Clipboard, screen and microphone permissions start off.
3. Use **Tools** or **Ctrl+Shift+K** to inspect a tool's arguments and run it locally. Optional fields are omitted unless selected; complex arrays use JSON.
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

SQLite has optional sensitive-field DPAPI protection, not whole-database encryption. Credentials have no plaintext fallback. Close Jarvix before manually copying its profile for backup, or use the consistent backup action. Tools call services; UI calls the application facade; providers never control the OS. The agent defaults to six model rounds, twelve model tool calls and thirty-two nested operations. Cancellation and deadlines are shared across nested actions.

## Verify and build

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build.ps1
```

Tests use isolated profiles, mocked provider HTTP and mocked destructive OS actions. Windows native read-only checks and packaged startup are verified separately. Live paid-provider requests and real microphone recognition need user hardware/accounts.

Run `.\.venv\Scripts\python.exe scripts\verify_operator_native.py` for an opt-in Windows UIA/capture/OCR smoke test that creates and controls only its own test windows. Local OCR needs an installed Windows recognition language; accessibility/capture support varies by application.

Native operator control requires Windows. The Windows-start trigger means the first Jarvix run after boot; closed-app schedules require separate Task Scheduler opt-in. Stop prevents subsequent work and closes supported active HTTP responses; connection establishment remains bounded by transport timeout. Completed side effects are not automatically rolled back. Wake-word support requires the optional `sherpa-onnx` package and a local keyword-spotting model. Real account APIs, microphone recognition and connected-browser behavior require configured accounts/devices and are not verified by mocked tests. See [SECURITY.md](SECURITY.md) and [verification](docs/VERIFICATION.md).
