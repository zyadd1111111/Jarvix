# Jarvix 0.3 — Operator checkpoint

Continues the existing services, tool registry, SQLite profile and thirteen-page UI.
The source installation and portable Windows packaging remain supported.

- Native accessibility control with expiring window/control identities, protected-field
  rejection, explicit pointer fallback, verified actions and visible pause/cancel HUD.
- Bounded Operator plans, previews, result references, postconditions, safe retries,
  pause/resume/cancel, local session history and guarded undo. Uncertain mutations
  require inspection and a new plan; successful earlier actions are not replayed.
- Workflow schedules/events, safe conditions, branches, delays, manual routines,
  exact background grants, vertical builder, history and independent live controls.
- Background workflows, reminders and notifications while running or in the tray;
  truthful shutdown waits for owned work to stop.
- Explicit context snapshots, optional hotkey overlay, command-center Home, chat
  action cards and workspaces with project/terminal and registered-window layouts.
- Isolated window capture, accessible screen text/error hints, local Windows OCR and
  before/after image comparison. No continuous recording or automatic cloud upload.

Verification: 396 tests passed, one native opt-in test passed separately, Ruff and
packaged startup passed. All 54 packaged modules match current source; 223 tools
are registered. The native test uses only owned Qt windows for typing/invocation,
stale-target rejection, password protection, isolated capture and local OCR.
The portable ZIP and Python wheel are built. Details are in `VERIFICATION.md`.

Limits: Windows-only native control; application accessibility/capture varies;
OCR requires an installed Windows recognition language. No cloud image attachments,
wake words, response token streaming, deep browser integration, live account OAuth,
recycle restoration or scheduling while Jarvix is closed. The Windows-start trigger
does not register autostart. Background workflows cannot perform UI input, arbitrary
commands, nested workflows or whole mutable-workspace launches. Expand scheduled
setups into separately approved safe actions. Live paid providers and real microphone
recognition still require hardware/account validation. This checkpoint is not published.

