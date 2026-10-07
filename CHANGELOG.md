# Changelog

All notable changes to Jarvix are documented in this file.

## [0.9.0] - 2026-10-05

- Rebuilt presentation with neutral design tokens, Segoe UI Variable, consistent SVG icons and grouped collapsible navigation.
- Main-window Operator, Missions, Knowledge and Skills workspaces; dedicated project rows and inspectors reuse existing actions.
- Command-focused Home, document-style Chat, compact tool results and category-based Settings.
- Sortable Files and process tables, native context menus, restrained workflow blocks, account rows and accessible dialogs.
- Existing backend, storage schema, provider execution and permission enforcement remain unchanged.

## [0.8.0] - Continuum - 2026-10-02

- Explicit personal context profiles persist references and memory pins, with access/scope/expiry checks and manual activation.
- Linked work checkpoints survive restart and preview current project observations and protected Operator recovery.
- Reviewed literal skills reuse verified sessions or manual routines, with fresh confirmation, recipe fingerprints and recorded usage.
- Owned background continuation uses existing safe metadata reads, deadlines and cancellation; uncertain writes are never replayed.
- Home/palette saved-work views, Operator learning/progress shortcuts and exact recipe approval previews.
- Source permission/enablement revalidation, including changes made during approval.
- Expiring structured Session Memory with explicit project/permanent promotion; linked checkpoints now include tasks, notes, Knowledge Spaces and apps.
- Skill edit/duplicate, metadata/versioning, test mode, reviewed import/export, explicit workflow proposals and bounded local pattern suggestions.
- Mission milestones/deadlines, linked Skills and calculated progress; graph relationship provenance, confidence, type and scope.
- Saved execution evaluation and routing profiles, including immediate local-only enforcement between provider rounds.
- Optional source-approved daily brief, notification grouping/snooze/category muting and related-work previews.
- On-demand diagnostics, bounded checksum-verified backups, approved backup schedules and isolated restore profiles with unattended work disabled.
- Explicit vault-backed device pairing and encrypted signed offline envelopes with expiry, revocation and replay protection; no remote execution.
- Developer release checklist and owned command verification; manual public release inspection and artifact checksum verification without installation.
- Asynchronous source-permissioned Knowledge/extension inspection; compact brief, health, evaluation, backup, device and update views.

## [0.7.0] - Fusion - 2026-10-01

- Project continuation previews and bounded specialist read handoffs reuse existing registered tools.
- Persistent Missions track linked work, progress and blockers without replaying actions on resume.
- Explicit local Context Graph with access revalidation and unverified external references.
- Compatible model fallback chains, Coding/Document Analysis roles, latency/failure metrics and fresh cloud disclosure.
- Optional local suggestions with reasons, persistent dismissal and category muting; off by default.
- Stronger app adapter dispatch and honest editor/browser/Explorer/owned-terminal context.
- Mission/suggestion UI, command-palette entry points and Chat model selection indicators.
- Fixed child workflow cancellation while its parent is paused.

## [0.6.0] - Adaptive - 2026-09-30

- Loopback Ollama/OpenAI-compatible providers, discovery, local-only mode and capability-aware task-role routing.
- Incremental local hybrid search and cited Knowledge Spaces, with live source/root validation and offline keyword fallback.
- Supervised Operator sessions, progress/dependency graphs, bounded step deadlines, safe handoff and restart recovery.
- Reviewed declarative extension SDK that preserves the host permission boundary and rejects changed manifests.
- Workflow variables, structured output references, bounded read-only loops, pinned subflows, templates and write-safe debugging.
- Guarded app adapters, Explorer context, Windows Search, virtual desktop inspection, opt-in Recent items and confirmed Jarvix startup registration.
- Optional transactional sensitive-field DPAPI migration with protected future writes and preserved corrupted profiles.
- Compact knowledge/model/extension UI, routing controls, safer worker shutdown and offline performance checks.
- Restored existing account connection and optional wake-mode UI; persistent supervision confirmations outlive launch workers.
- Packaging rejects changing source, checks frozen modules against the checkout and verifies both executables are x64.

## [0.5.0] - Intelligence - 2026-09-29

- Cited, local PDF/Office/text extraction, bounded pagination, section/table search, extractive summaries/Q&A/comparison and project knowledge collections.
- Dependency failure handling, reviewable recovery plans and preserved successful outputs with uncertain-write replay protection.
- Permissioned cross-app plan templates, account briefings, scoped/provenance-aware memory and ephemeral conversation context.
- Automation undo, inverse previews, partial rollback reporting and cancellable queued requests/HTTP body reads.
- Browser form privacy, optional download inspection, page evidence, tab search and guarded duplicate cleanup.
- Document/source UI, recovery editor, memory provenance and explicit connected-tab search in the command palette.
- Portable build includes the native browser host and extension. Accounts, devices and paid-model calls still need live configuration/testing.

## [0.4.0] - Nexus - 2024-09-28

### Added

#### Browser Automation
- Native messaging bridge for Chrome and Edge with tab management, page inspection and element targeting
- Accessibility-first automation: find links/buttons/inputs by text, click elements, type into fields
- Tab operations: list, open, close, switch, duplicate, reload, navigate back/forward
- Page operations: read title/URL, inspect structure, scroll, search, extract selected text
- Session management: save/restore browser sessions, group tabs, close duplicates
- Page capture: summarize current page, save page to Jarvix project
- Security boundaries: sensitive website actions use existing permission system, no CAPTCHA/login bypasses

#### Real Integrations
- OAuth authorization flows for Google, GitHub, Spotify and Discord
- IntegrationService with connection state tracking, credential vault storage and adapter registry
- Gmail: search mail, read messages, summarize inbox, draft replies, labels/archive (sending requires confirmation)
- Google Calendar: read events, search, check availability, create/update/cancel with confirmation
- Google Drive: search files, inspect metadata, open documents, create/upload/download where supported
- GitHub: repositories, issues, pull requests, notifications, branches, commits (mutations require confirmation)
- Spotify: current playback, play/pause, next/previous, search music, device selection
- Discord: read permitted channels, summarize activity (posting requires confirmation)
- Integration Center UI: connection cards with status, connected account, permissions, last activity
- Scoped permissions and token refresh handling
- Never fake integration results or claim connected status without verified auth

#### Streaming
- Token-level streaming from OpenAI and Gemini providers
- Incremental UI rendering in chat with stream_buffer, stream_widget and flush_stream
- Clean cancellation mid-stream with proper state persistence
- No duplicate partial messages or blocking UI thread

#### Multimodal Chat
- Image and screenshot attachments for vision-capable models (OpenAI GPT-4V, Gemini Pro Vision)
- ImageComposer UI: drag-drop images, paste from clipboard, attach files
- Active-window screenshot and selected-monitor screenshot capture
- Local disclosure approval before sending images to cloud providers
- Provider vision capability flags (supports_vision)
- Attachment tracking in chat_attachment records

#### Voice & Wake Word
- WakeWordService with optional local keyword spotting via sherpa-onnx
- Wake phrase "Jarvix" activates hands-free voice mode
- Interruption commands: "stop", "cancel", "never mind"
- Visible microphone indicator with sensitivity control and instant pause
- Audio stays local; no continuous cloud streaming or hidden recording
- Integration with existing SpeechInputService for post-wake transcription
- Model validation and bounded audio queue discarded on stop

#### Scheduling
- SchedulerService for Windows Task Scheduler integration
- Weekday/time schedules for approved workflows that run even when Jarvix is closed
- HMAC-signed task definitions with fingerprint verification
- Least privilege, interactive user context, no stored passwords
- Explicit per-workflow approval with snapshot binding to workflow and app configurations
- Task XML generation with proper triggers, principals and execution settings
- scheduler.install, scheduler.preview, scheduler.list and scheduler.remove tools

#### Recycle Restoration
- RecycleService tracks Jarvix-deleted items with Windows Shell receipts
- List recently recycled items with original paths and availability status
- Preview restore operations with conflict detection
- Restore with fingerprint verification to confirm correct file returned
- files.recycled, files.restore_preview and files.restore tools
- Activity logging and restored flag tracking
- No false restore promises when Windows no longer has the item

#### Context & Operator
- ContextService with richer snapshots: active window, workspace, project, selected files, clipboard type
- Explicit opt-in permission model (context.enabled setting)
- context.inspect and context.select tools for session context management
- Operator checkpoint support for resumable sessions
- Improved failure reporting with structured error reasons
- Dependency-aware step execution (already existed, verified working)

### Changed

- Storage schema bumped to v4 with `integrations`, `scheduled_tasks` and `attachments` column in messages
- Services.py imports and registers integration, scheduler, context, recycle modules
- UI version label updated from "OPERATOR / 0.3" to "OPERATOR / 0.4" in window.py
- README updated to reflect 0.4.0 Nexus features and remove "unimplemented" notes for completed features
- Integration adapters no longer show fake "Not connected" states; OAuth flows work end-to-end
- Browser operations use structured automation APIs instead of screen coordinates where possible

### Security

- All external account actions respect Jarvix's 3-level permission system
- Level 1: read-only operations
- Level 2: reversible/control actions  
- Level 3: always requires fresh confirmation (sending email, posting messages, destructive actions)
- OAuth tokens stored in OS credential vault, never in SQLite or logs
- Scheduled tasks use signed definitions; arbitrary commands cannot be injected
- Wake-word audio processing stays local; only speech after wake event is transcribed
- Multimodal attachments require explicit disclosure approval before cloud submission
- Browser automation respects security prompts; never types into password/credential fields autonomously

### Technical

- 30 capability modules (integration.py, scheduler.py, context.py, recycle.py added)
- ~15,700 lines of Python across core, capabilities, providers and UI
- IntegrationService with adapter protocol and OAuth state machine
- SchedulerService with Windows Task Scheduler backend and HMAC signing
- RecycleService with PowerShell Shell.Application COM bridge
- WakeWordService with sherpa-onnx KeywordSpotter integration
- Browser native messaging with Chrome/Edge extension protocol
- Token streaming with QTimer-based flush and proper markdown rendering
- Vision model support flags in provider implementations

## [0.3.0] - Operator - 2024-09-16

### Added
- Windows UI Automation for control inspection and targeting
- Operator plans with action previews, result references and verification
- Workflows with schedules, triggers, conditions and background runtime
- Explicit window/monitor capture with local OCR
- Optional global command overlay and context snapshots
- Workspace window layouts and guarded undo

### Changed
- Home surfaces operator progress and automation status
- Automations page with workflow builder and live controls
- Chat action cards with open/view/undo actions

## [0.2.0] - Foundation

### Added
- Core services: Files, Developer, Computer, Productivity, Desktop
- SQLite storage with Repository pattern
- OpenAI and Gemini provider adapters
- 3-level permission system
- Tool registry and orchestrator
- PySide6 UI with 13 sections
- Local speech synthesis
- Notes, tasks, memory, projects, apps
- Safe developer tools with terminal integration

---

Version format: [major.minor.patch] - Codename - Date
