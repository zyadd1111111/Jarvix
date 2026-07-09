# Jarvix V.01

Jarvix is a local-first desktop AI assistant starter inspired by JARVIS-style
workflows. V.01 is intentionally realistic: it gives you a clean desktop app,
AI chat, local notes, voice input, text-to-speech, simple app/website actions,
system info, and a safety-first command runner without pretending to be a
movie-level AI.

Current version: `0.1.0`

## What Jarvix Can Do

- Chat with Gemini or OpenAI using API keys from your shell environment.
- Run local chat actions such as opening websites, opening apps, and doing math.
- Accept push-to-talk voice input and send the transcript into the chat workflow.
- Speak responses through local text-to-speech.
- Create and search local SQLite notes.
- Show CPU, RAM, battery, and storage information.
- Open macOS apps and websites only after confirmation.
- Validate and run a tiny allowlist of safe read-only commands with confirmation.
- Present a modern liquid-glass PySide6 interface with sidebar navigation and a
  right utility rail.

## Safety

- Jarvix does not read passwords, cookies, tokens, banking data, or private accounts.
- API keys are read from environment variables only.
- AI chat cannot run shell commands automatically.
- Commands require confirmation in the UI and pass through a strict allowlist.
- Dangerous commands, shell metacharacters, redirects, pipes, network tools, and
  sensitive credential-related strings are blocked.
- Local databases, virtual environments, and real `.env` files are ignored by Git.

## Requirements

- macOS for V.01 app launching behavior.
- Python `3.11+`; tested locally with Python `3.13.3`.
- A Gemini API key or OpenAI API key for live AI chat.
- Microphone permission if you want voice input.

## Install

```bash
cd /Users/zain/Documents/Jarvix
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Configure Gemini

```bash
export JARVIX_AI_PROVIDER="gemini"
export JARVIX_MODEL="gemini-3.5-flash"
export GEMINI_API_KEY="your-key"
```

Gemini API keys from Google AI Studio can start with `AQ` for newer auth keys or
`AIza` for older standard keys.

Copy a key to your clipboard, then set and check it without printing the secret:

```bash
export GEMINI_API_KEY="$(pbpaste | tr -d '\n\r')"
python -c "import os; k=os.getenv('GEMINI_API_KEY',''); print('gemini key set:', bool(k), 'starts right:', k.startswith(('AQ','AIza')), 'length:', len(k))"
```

## Configure OpenAI

```bash
export JARVIX_AI_PROVIDER="openai"
export JARVIX_MODEL="gpt-5.5"
export OPENAI_API_KEY="your-key"
```

Jarvix will still launch without an API key and will show a Settings warning.

## Run

```bash
python src/main.py
```

Or after editable install:

```bash
python -m main
```

## Chat Actions

The Chat tab handles a few safe local actions directly before falling back to the
AI provider:

```text
open youtube
can you open a new tab for github
launch app Calculator
calculate 15% of 200
what is 2 plus 2
```

Website and app launches still ask for confirmation before Jarvix opens anything.
Voice transcription is sent through the same chat workflow.

## Safe Commands

The System tab can run only a tiny read-only allowlist after confirmation:

```text
pwd
ls
ls -la
date
whoami
uptime
df -h
uname -s
python3 --version
```

Everything else is blocked in V.01.

## Test

```bash
pytest
```

The core service tests do not require desktop, voice, Gemini, or OpenAI credentials.

## v1 Features

- PySide6 desktop window with Chat, Voice, Notes, Apps, System, and Settings sections
- Liquid-glass dark UI with chat bubbles, sidebar status card, and utility rail
- OpenAI Responses API or Gemini Interactions API chat client with environment-only secrets
- SQLite-backed notes at `data/jarvix.db`
- faster-whisper push-to-talk transcription wrapper
- sounddevice/soundfile microphone capture for push-to-talk voice input
- pyttsx3 text-to-speech wrapper
- macOS app launcher and safe website launcher
- psutil system snapshot for CPU, RAM, battery, and storage
- safe command validation and confirmation-based command execution

## Important Files

```text
src/main.py                 App entrypoint
src/ui/main_window.py        PySide6 desktop UI
src/ai/client.py             Gemini/OpenAI provider wrapper
src/tools/chat_actions.py    Chat action router for local tasks
src/tools/calculator.py      Safe arithmetic evaluator
src/memory/notes_store.py    SQLite notes storage
src/safety/command_safety.py Safe command allowlist
tests/                       Automated tests
```

## Not in v1

- Browser automation
- Calendar integration
- Email sending
- File search
- Project memory
- Plugin system

## GitHub Release Commands

For maintainers publishing V.01:

```bash
git status -sb
PYTHONPATH=src python -m pytest -q
git add -A
git commit -m "Release Jarvix V.01"
git tag -a v0.1.0 -m "Jarvix V.01"
git push -u origin main
git push origin v0.1.0
```
