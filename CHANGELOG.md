# Changelog

## Jarvix V.01 / 0.1.0

Initial local desktop assistant release.

### Added

- Liquid-glass PySide6 desktop interface.
- Chat with Gemini Interactions API or OpenAI Responses API.
- Local chat actions for opening websites, launching apps, and calculations.
- Voice input pipeline using faster-whisper with microphone capture dependencies.
- Text-to-speech through pyttsx3.
- SQLite local notes with create/search support.
- System information panel powered by psutil.
- Safe command runner with confirmation and strict allowlist.
- Automated tests covering safety, notes, launchers, settings, AI provider routing,
  calculations, chat actions, voice fallback, and UI smoke behavior.

### Fixed

- Gemini `AQ` auth keys are now supported.
- Gemini uses the current Interactions API path instead of the older generateContent path.
- Voice transcripts now submit into the chat workflow instead of only filling the input.
- Chat can execute safe local actions after confirmation without switching tabs.
- Chat bubble text renders apostrophes and special characters correctly.

### Notes

- V.01 is macOS-first for app launching.
- Browser automation, calendar, email workflows, file search, and plugin systems are planned
  for later versions.

