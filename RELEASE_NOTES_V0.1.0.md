# Jarvix V.01 Release Notes

Jarvix V.01 is the first working local desktop assistant release.

## Highlights

- Modern liquid-glass desktop UI.
- Gemini and OpenAI chat support.
- Local actions from chat: open websites, launch apps, and calculate expressions.
- Push-to-talk voice input routed into chat.
- Local TTS, notes, system info, and safe command execution.
- Safety-first defaults: no secrets stored in source, no autonomous shell commands, and
  confirmation before external launches.

## Install

```bash
cd /Users/zain/Documents/Jarvix
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Run With Gemini

```bash
export JARVIX_AI_PROVIDER="gemini"
export JARVIX_MODEL="gemini-3.5-flash"
export GEMINI_API_KEY="$(pbpaste | tr -d '\n\r')"
python src/main.py
```

## Run Tests

```bash
PYTHONPATH=src python -m pytest -q
```

## Validation

V.01 was validated with the automated test suite:

```text
54 passed
```

