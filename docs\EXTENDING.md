# Extending Jarvix

## A new tool

Add a service under `src/jarvix/capabilities/` and call its `setup(services, registry)` before the catalog setup. Register a `ToolSpec` with a unique dotted name, description, closed JSON schema, permission scope, risk and explicit permission level (1/2/3). Return bounded `ToolResult` data; nested failures must propagate through `ToolResult.ok`. Local data must retain `sensitivity="local"`.

Use `services.execute_tool` for UI, automation and nested dispatch so approvals, cancellation, deadlines and shared step limits apply. Never call `registry.execute` from an agent-facing composite handler. The catalog loads tool families on demand; schemas also generate local action forms. Do not route natural-language phrases in Chat. Background actions remain restricted by the host allowlist.

Test invalid input, expected success, permission denial, path boundaries and output/error redaction. Handlers are trusted in-process code; third-party plugin loading requires a future process sandbox.

## A new provider

Implement `AIProvider.complete(messages, tools, model) -> Completion`, translating the domain contracts to the provider's protocol. Preserve opaque function-call metadata in `Message.metadata`. Register a factory and default model in `providers/__init__.py`. Update credential provider allowlists and provider selectors. Use transport mocks to test whole tool cycles, not only plain-text answers.

Do not bind an SDK to Qt widgets. All requests run in workers; all permission widgets execute on the UI thread. Provider errors must be safe messages without request headers, bodies or API keys.

## Persistence

All SQL belongs in repository/service boundaries. Connections are short-lived, transactions rollback on failure and SQLite foreign keys are enabled. Database schema version is tracked with `PRAGMA user_version`; add numbered migrations for subsequent versions. Never persist API keys in settings.

## Account integrations

Build a service adapter and register narrowly scoped tools. Add OAuth consent, token renewal and vault persistence before making an integration active. Email drafts can be local records; sending requires a distinct tool and permission. Do not mark a roadmap connector as connected before a real authentication and API contract exists.

## Voice

`voice.py` owns local playback; `speech_input.py` captures opt-in microphone audio and transcribes locally through Windows System.Speech. Keep status and cancellation visible, remove temporary audio, and leave transcripts as editable drafts. Continuous dictation requires a per-session opt-in. Wake-word listening is not implemented.
