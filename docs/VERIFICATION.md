# Jarvix 0.3 — Operator verification

Checkpoint completed September 27, 2026. Windows 10 x64, Python 3.11.0,
PySide6 6.11.2 and PyInstaller 6.22.3. Existing architecture and tests preserved.

- **396 tests passed, 1 opt-in native test skipped** in the full suite (115.34 seconds).
  Saved evidence: `artifacts/operator-0.3-tests.log` and `operator-0.3-tests.xml`.
- The skipped Windows integration test **passed separately** with explicit opt-in.
  It creates its own Qt windows and checks real UIA typing/invocation, stale identity
  rejection, password protection, isolated capture under occlusion and local en-US OCR.
- **Ruff and dependency validation passed.** Read-only native system, app and window
  checks passed; disabled clipboard/screen gates rejected access.
- Coverage includes permissions, file roots, structured plans/references, loop detection,
  postconditions, safe retries, session persistence, undo conflicts, workflow branches,
  scheduling, cancellation isolation, shutdown and live UI controls. Mocked provider
  flows cover natural-language automation proposals and fresh save confirmation.
- **223 registered tools.** All 54 packaged Jarvix Python modules match current source.
- Windows executable, portable ZIP and Python wheel built successfully. Packaged
  startup rendered Home using an isolated profile and exited with code 0. Home and
  the workflow builder were visually checked; the builder also shut down cleanly.

Artifacts: `dist/Jarvix/Jarvix.exe`, `dist/Jarvix-0.3.0-windows-x64.zip`, and
`dist/jarvix-0.3.0-py3-none-any.whl`. This is an unsigned local build, not a published release.

Live paid-model calls, actual microphone recognition and external account APIs remain
unverified. Native control depends on each application's accessibility support; OCR
needs an installed Windows language. Scheduling requires Jarvix to remain running.
Other deliberate limits are in README and OPERATOR-CHECKPOINT; permissions and data
boundaries are in SECURITY.md. No unrelated user windows were captured by native tests.
