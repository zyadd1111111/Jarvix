# Initial foundation verification

Verified on Windows 10 x64 with Python 3.11.0, PySide6 6.11.2 and PyInstaller 6.22.3. The implementation was built from a fresh directory; no previous Jarvix implementation was reused.

## Results

- **149 tests passed**, with successful process exit after Qt teardown.
- **Ruff passed** for source, tests and Python scripts.
- Both real AI adapters completed mocked provider → orchestrator → permission → built-in tool → SQLite → final response flows. No live or paid AI calls were made.
- Twelve desktop tests cover all thirteen requested sections, local editing, no-key chat, permission dialogs on the GUI thread, safe worker shutdown, context exclusion, root revocation, voice status and routine-result inspection.
- File boundary tests include symlink and Windows junction escapes, root retargeting and revocation during an active scan.
- Duplicate tool IDs and repeated identical side-effect requests cannot execute the same action twice in one run.
- Home, Chat, Files and Settings were rendered and visually inspected. Windows offscreen font registration was corrected after the first render.
- Windows reports installed local System.Speech voices. Speech process behavior, UTF-8 input, rate bounds and stop handling are tested with a subprocess fake; audible playback was not manually evaluated.
- The Python wheel was built successfully.
- The packaged Windows executable started, created an isolated SQLite profile, rendered its Home window to `artifacts/packaged-home.png` and exited with **code 0** on September 17, 2026.

## Packaging fix

The first packaged startup failed because PyInstaller selected a versioned ICU library from an unrelated Poppler runtime on the machine's PATH. Qt requires the Windows ICU exports. The spec now isolates DLL discovery to the active Python/Qt runtime and Windows directories. The application then started successfully. `scripts/smoke-package.ps1` verifies actual packaged startup and rendering after future builds.

## Remaining validation boundaries

API authentication, paid live model responses, actual email/calendar/Spotify/GitHub connectors, microphone input, background operation after app exit, signed installers and non-Windows deployment are outside this initial verified foundation. The adapters and tool loop are tested offline; production provider accounts and network conditions still need live acceptance testing after keys are configured.

The full capability list and deliberate limits are in the README. Data and permission boundaries are in SECURITY.md.
