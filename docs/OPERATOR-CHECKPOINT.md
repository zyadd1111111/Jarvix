# Jarvix 0.2 operator checkpoint

This extends the existing 0.1 architecture and thirteen-page desktop shell.
The registry contains 164 structured tools (149 additions to the original 15).
Inspect the current schemas through Actions or Settings; no cloud key is needed.

Delivered: native window/system controls, root-confined file operations and undo,
clipboard/screenshot opt-ins, local dictation, workspaces/apps/browser shortcuts,
task/note/memory/project metadata, developer command sessions, manual and scheduled
routines, notification history/tray, and conversation organization/action timelines.
SQLite migration 1→2 is additive. Account adapters remain Not connected.

Safety checks include immediate Level 3 confirmation, fresh app-argument approval,
nested permission/cancellation limits, denied-routine propagation, archive/reparse
boundaries, Git helper suppression and filtered diffs, and redacted audit records.

Verification completed for the 0.2.0 release: 283 tests passed, Ruff passed, native read-only checks passed, packaged startup passed, and the wheel and portable Windows ZIP were built.

Unimplemented: wake words, vision/OCR/image attachments, response token streaming,
deep browser automation, live account APIs/OAuth, recycle restoration, and closed-app
scheduling. Scheduled routines intentionally support only local summaries and
notifications; manual routines support other registered tools with per-action consent.
Real microphone recognition and paid provider calls need hardware/account validation.
Windows mutations are mocked in tests; read-only native inspection runs separately.

