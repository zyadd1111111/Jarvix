# Jarvix 0.5 — Intelligence verification

September 29, 2026 · Windows x64 · Python 3.11 · PySide6.

- Full regression suite: **497 passed, 1 optional native test skipped** (241.57 seconds).
- Ruff and `pip check`: passed. No paid model or real account API requests were made.
- Packaged desktop startup and browser-helper framing checks passed. Both executables
  are x64; 65 packaged application modules match source. The portable ZIP passed CRC
  validation, and its Home screen was visually checked using an isolated profile.
- Focused checks cover document parsing/citations/bounds, memory scopes/conflicts,
  ephemeral context isolation, planner recovery, nested permissions, rollback,
  stalled HTTP cancellation, browser form privacy and optional download access.
- Offscreen Qt flows exercise document continuation, source views, explicit tab
  lookup, context revocation, provenance and recovery/undo controls.
- Application facade startup measured 0.743 seconds here, with 332 tools.
  Palette construction reuses tool schemas; document contents are read on demand.

`scripts/build.ps1` gates packaging on the full suite, Ruff and dependency checks.
It builds both executables, checks browser helper framing without registering it,
and renders the packaged UI using an isolated profile. Build evidence:
`artifacts/intelligence-0.5-build.log`.

Outputs: `dist/Jarvix/Jarvix.exe`, `dist/Jarvix/JarvixBrowserHost.exe`,
`dist/Jarvix-0.5.0-windows-x64.zip` and `dist/jarvix-0.5.0-py3-none-any.whl`.
These are unsigned local artifacts; building does not publish a GitHub release.

Live browser connections, account APIs, microphone recognition and paid model calls
require configuration and are not verified by mocked tests. Local summaries/Q&A
are cited extracts; scanned PDF OCR, semantic memory matching and implicit cloud
uploads are not performed. Parsing and output sizes are bounded. Completed side
effects remain until a supported inverse is explicitly requested.
