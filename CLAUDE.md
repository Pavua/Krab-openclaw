# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Краб (Krab) is a production Telegram userbot (MTProto, not Bot API) that routes messages to AI backends. It runs as the owner's personal Telegram account, not a bot token. The AI routing chain is: OpenClaw Gateway (port 18789) → LM Studio (local) or Gemini (cloud fallback).

## Commands

```bash
# Run the bot (requires .env with Telegram credentials)
python -m src.main

# Run all tests
pytest tests/

# Run a single test file
pytest tests/unit/test_access_control.py

# Run a single test
pytest tests/unit/test_access_control.py::test_owner_can_do_everything -v

# Lint
ruff check src/

# Format
ruff format src/
```

**macOS launchers** (double-click in Finder):
- `new start_krab.command` — canonical launcher with auto-restart, OpenClaw gateway management, watchdog
- `new Stop Krab.command` — graceful shutdown via stop-flag
- `Start Voice Gateway.command` — starts the Voice Gateway (separate repo at `../Krab Voice Gateway/`)

**Test config** (`pyproject.toml`): `asyncio_mode = "auto"`, `addopts = "-v --tb=short"`. All tests are async-friendly via `pytest-asyncio`.

## Architecture

### Entry point and lifecycle

`src/main.py` → `src/bootstrap/runtime.py` manages the full lifecycle: health checks, creating `KraabUserbot` + web panel, signal handling, graceful shutdown.

### Message flow

```
Telegram message
  → src/userbot_bridge.py (KraabUserbot, Pyrogram)
      → _make_command_filter() — ACL check before handler runs
      → src/handlers/command_handlers.py — specific command (group=-1, runs before default)
      → default handler: src/openclaw_client.py → OpenClaw Gateway (SSE stream)
          fallback: src/model_manager.py → LM Studio or Gemini
```

Commands are registered in `userbot_bridge.py` with `filters.command("name", prefixes=prefixes)` at `group=-1` so they fire before the catch-all AI handler. The `prefixes` variable comes from `Config.TRIGGER_PREFIXES` (default: `!`, `/`, `,`, `@`).

### Key modules

| Module | Role |
|--------|------|
| `src/config.py` | All config — one `Config` class, env vars with fallback chains |
| `src/userbot_bridge.py` | `KraabUserbot` Pyrogram client — command registration, ACL enforcement, session management |
| `src/openclaw_client.py` | OpenClaw Gateway client — SSE streaming, semantic guards, fallback |
| `src/model_manager.py` | LM Studio + Gemini routing, auto-unload idle models |
| `src/handlers/command_handlers.py` | All `!command` handlers (one function per command) |
| `src/modules/web_app.py` | FastAPI dashboard on port 8080 — control panel, health endpoint `/api/health/lite` |
| `src/core/access_control.py` | ACL — `AccessLevel.OWNER/FULL/PARTIAL/GUEST`, `OWNER_ONLY_COMMANDS` set |
| `src/core/scheduler.py` | Reminder scheduler (cron-like, persisted) |
| `src/core/inbox_service.py` | Multi-chat inbox state machine |
| `src/integrations/macos_automation.py` | macOS AppleScript automation via `osascript` |
| `src/integrations/browser_bridge.py` | Playwright CDP bridge to existing Chrome at `localhost:9222` |
| `src/modules/perceptor.py` | STT via Voice Gateway `/stt` endpoint |

### Configuration critical details

`Config.GEMINI_API_KEY` resolution order: `GEMINI_API_KEY_PAID` → `GEMINI_API_KEY` → `GEMINI_API_KEY_FREE`. Use the paid key to avoid RPM-throttle silent timeouts that look like provider failures but aren't 429s.

`TELEGRAM_ALLOW_INTERACTIVE_LOGIN` defaults to `0`. Interactive Telegram re-login must be done via `telegram_relogin.command`, not through the regular startup path.

OpenClaw Gateway config at `~/.openclaw/openclaw.json` is only read at gateway startup — changes require a gateway restart (not just a Краб restart).

### ACL system

Every command handler checks ACL before executing. The flow: `_make_command_filter(cmd_name)` → checks if `cmd_name in OWNER_ONLY_COMMANDS` → if owner message, allow; else check `AccessLevel`. Adding a new owner-only command requires adding it to `OWNER_ONLY_COMMANDS` in `src/core/access_control.py`.

### Adding a new command

1. Add handler function `async def handle_foo(bot, message)` to `src/handlers/command_handlers.py`
2. Export it from `src/handlers/__init__.py`
3. Register in `userbot_bridge.py` `_setup_routes()`: `@self.client.on_message(filters.command("foo", prefixes=prefixes) & _make_command_filter("foo"), group=-1)`
4. If owner-only, add `"foo"` to `OWNER_ONLY_COMMANDS` in `src/core/access_control.py`

### Translator stack

`src/core/translator_runtime_profile.py` — persisted settings (language pair, mode, voice strategy). `src/core/translator_session_state.py` — per-session state. Both are JSON-backed via `data/translator/`. The `KraabUserbot.update_translator_runtime_profile(**changes, persist=True)` method is the canonical write path.

### Testing patterns

Tests mock external services (Telegram, LM Studio, OpenClaw). Key fixtures in `tests/unit/conftest.py`. The `tests/unit/test_userbot_startup.py` is the closest thing to an integration smoke test without live Telegram credentials.

### Runtime state files

Lock files, PID files, and stop-flags live in `~/.openclaw/krab_runtime_state/` (not in the repo). The repo root has `data/` for persisted app state (translator profiles, scheduler reminders, sessions).

## Conventions

- All comments, docstrings, and log messages are in Russian (Cyrillic).
- Logging uses `structlog` with keyword arguments: `logger.info("event_name", key=value)`. No f-string interpolation in log calls.
- Exceptions: `KrabError` hierarchy in `src/core/exceptions.py`. `UserInputError` for user-facing validation (renders as Telegram reply). `ProviderError` for AI backend failures.
- `from __future__ import annotations` at top of every module (Python 3.11+ deferred evaluation).
- Async throughout — no blocking I/O in handlers. Use `asyncio.create_subprocess_exec` not `subprocess.run` for shell commands.
