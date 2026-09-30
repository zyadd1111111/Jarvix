# Jarvix foundation

Jarvix is a new local-first desktop assistant, built from an empty directory. The first release establishes a working extensible system rather than pretending to implement every future integration.

## Decisions

- Python 3.11+ and PySide6 provide a native desktop process, accessibility, keyboard navigation, background workers, and access to operating-system services. A webview/Electron front end would add a second runtime without benefiting the initial tools.
- SQLite stores conversations, notes, explicit memories, tasks/reminders, registered projects, settings, scoped grants, and auditable actions. Repositories own SQL. Credentials belong in the OS credential vault, never SQLite.
- Provider adapters expose one normalized `complete(messages, tools, model)` contract. OpenAI and Gemini translate provider-specific JSON at the boundary. The UI never imports an AI SDK.
- The orchestrator owns bounded planning/tool loops. Every call is schema-validated, permission-checked and audited. Tool results are structured. No natural-language phrase routing or arbitrary shell tool exists.
- Network requests run off the UI thread. With no API key, local workspaces still function and chat explains configuration instead of fabricating AI answers.
- Only the current conversation and explicitly enabled tool definitions enter a provider request. Local tool results require per-run approval before transfer; this is separate from permission to execute the tool. No background bulk context upload, passive file indexing, or automatic memory harvesting.
- File search is confined to user-selected roots. App launching uses registered executable paths with argument arrays; links allow HTTP(S) only. No arbitrary deletion, command execution, email sending, or external-account writes are in the first release.
- Reminders work while the desktop process is running. Automation is explicit and bounded: unattended runs are limited to safe local summaries and local notifications. OS speech engines provide read-aloud where available; voice-input capability must report unavailable engines honestly.

## Boundaries

`domain.py` defines provider messages, tool specifications, call/results and permissions.
`storage.py` provides local persistence. `security.py` provides vault-backed credentials and permission decisions.
`providers/` contains OpenAI and Gemini adapters. `tools/` contains typed capabilities and a registry.
`orchestrator.py` is independent of Qt. `services.py` composes local services. `ui/` contains only presentation and worker plumbing.

## Acceptance

1. A packaged entry point launches all thirteen requested navigable sections in a polished desktop shell.
2. Notes, memories, tasks, settings and conversation metadata persist across restarts in an isolated data directory.
3. Provider adapters normalize function calls and failures; fake transports test their boundaries without paid calls.
4. A scripted provider exercises planning → permission → tool → disclosure approval → answer. Denial and loop limits have tests.
5. File tools cannot read outside configured roots; credentials are excluded from local exports and logs.
6. Ctrl+K opens searchable local commands, tools, notes, registered apps, indexed files and settings.
7. UI smoke tests and rendered screenshots check startup, navigation, worker completion and layout; unit tests cover meaningful security/storage behavior.
8. Documentation separates implemented functionality, configuration prerequisites and future integration points.

## Future evolution

Add migrations rather than changing deployed schemas in place; add adapters and registered tools rather than modifying chat routing. A future sandbox process should isolate third-party plugins. OAuth integrations need consent scopes, refresh-token vault storage, rate limits and provider-specific contract tests before activation. Continuous activity tracking and microphone capture require separate explicit opt-in.
