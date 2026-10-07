# Jarvix 0.9 verification

October 7, 2026 · Windows x64 · Python 3.11 · PySide6.

- Legacy remains the default. Settings → Appearance → Interface persists Legacy/Nexus for the next launch, preserving current drafts and workers. Nexus styling stays scoped to its window; both presentations use the existing services and local profile.
- All **80 baseline backend modules are unchanged**. Existing UI changes are limited to the Interface setting and presentation-constructor hooks; the Legacy theme and page implementations are retained.
- Full regression: **779 passed, 1 optional native test skipped** in 745.11 seconds. Ruff and dependency checks pass. Focused checks cover interface persistence, draft preservation, window-scoped styles, controller ownership, real action routes, knowledge permissions, material caching and default-deny confirmations.
- Reviewed 165 Nexus captures across **15 populated/empty profiles**, including Home, Chat, Operator, Missions, Knowledge, Files, Automations, Integrations, System, Settings and Appearance. Tested 860×600, 1280×840, 1920×1080 and 2560×1440 layouts at 100–200% scaling, including 3840×2160 output. All 15 Legacy before/after comparisons are pixel-identical.
- Native Windows review confirmed dropdown arrows, checked indicators and the Qt material fallback. The test host exposes one physical 1920×1080 monitor; the broader DPI/resolution matrix uses isolated offscreen renders. Native Mica/Acrylic and physical multi-monitor behavior were not verified on this host.
- Native offline profiling measured 1.74-second startup, 228 MB resident memory, 0% single-core idle CPU over the two-second sample, and 77.51 ms average resize plus forced capture. Blur is cached per desktop canvas; painting never reads settings or captures other applications. These are one-host measurements, not performance guarantees.
- Both executables are x64, all **109 frozen application modules match source**, and the browser helper passes its framed-input check without changing registration. Fresh packaged Legacy, Nexus and Nexus Settings launches all exit successfully using the native Windows Qt platform. The packaged Nexus Home and Settings screens were visually inspected.

Full regression and package results are recorded in `artifacts/nexus-build.log`; visual/profile results are in `artifacts/nexus-matrix.log`, `artifacts/nexus/performance-windows.json` and `artifacts/nexus-package-results.json`. The build rejects application source changes during verification and compares frozen modules with source.

Reproduce renders with `python scripts/review_nexus.py --matrix` and profiling with `python scripts/profile_nexus.py`; both use isolated offline profiles. Reproduce packaging with `scripts/build.ps1`.

Outputs: `dist/Jarvix/Jarvix.exe`, `dist/Jarvix/JarvixBrowserHost.exe`, `dist/Jarvix-0.9.0-windows-x64.zip` and `dist/jarvix-0.9.0-py3-none-any.whl`. These are unsigned local artifacts. Building does not publish a GitHub release.
