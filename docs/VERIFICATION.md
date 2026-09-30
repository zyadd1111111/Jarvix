# Jarvix 0.6 — Adaptive verification

September 30, 2026 · Windows x64 · Python 3.11 · PySide6.

- Full regression suite: **590 passed, 1 optional native test skipped** in 277.78 seconds.
- Ruff and dependency checks pass. No paid AI requests or live account API calls were used in tests.
- Both packaged executables are x64; **84 frozen application modules match source**. The build rejects source changes during verification/packaging.
- Packaged desktop startup and browser-helper framing pass using an isolated profile. Home was visually inspected.
- Focused coverage includes local providers, routing, offline/hybrid search, source/root/account revalidation, Knowledge Spaces, manifest permissions, supervision, restart recovery, deadlines, detached confirmation ownership, workflow references/subflows, undo and DPAPI migration/rollback.
- Offline synthetic profile: facade startup **685 ms**, UI shell **330 ms**, read 100 notes **1.96 ms**, index 100 notes **1,155 ms**, unchanged refresh **17.79 ms**, keyword query **17.53 ms**. These are local measurements, not performance guarantees.

Build evidence: `artifacts/adaptive-build.log`. Reproduce with `scripts/build.ps1`; use `scripts/profile_local.py` for disposable offline timings.

Outputs: `dist/Jarvix/Jarvix.exe`, `dist/Jarvix/JarvixBrowserHost.exe`, `dist/Jarvix-0.6.0-windows-x64.zip` and `dist/jarvix-0.6.0-py3-none-any.whl`.
These are unsigned local artifacts. Building does not publish a GitHub release.

Live accounts, browser peers, local inference/embeddings, microphone/wake models and app-specific behavior still require configuration and live verification. Optional sherpa-onnx/model files are not bundled in the default portable build. Generic local endpoints without capability metadata cannot be selected automatically for unknown capabilities. Loopback transport cannot prove a separately configured inference server never proxies cloud requests.

Knowledge Q&A/summaries are cited extracts; Drive knowledge contains explicitly imported metadata. Replacing weights under the same embedding model tag requires a forced rebuild. SDK v1 composes reviewed host tools rather than loading arbitrary plugin code; restart refreshes AI catalog families after adding aliases.

DPAPI protects selected sensitive fields, not the entire database. Pre-existing backups keep their original policy. Completed side effects remain until a supported inverse is explicitly requested; uncertain writes are not silently replayed.

