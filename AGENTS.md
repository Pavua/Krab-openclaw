# AGENTS.md

> **AI Coding Assistant Instructions** - This document guides AI tools (GitHub Copilot, Cursor, Claude, etc.) on how to work with this codebase effectively.

---

## Project Overview

**Description**: Krab AI Userbot — Personal AI Assistant based on Python 3.13 & Pyrogram, integrated with OpenClaw Gateway.

**Tech Stack**:
- **Core**: Python 3.13+
- **Telegram Lib**: Pyrogram 2.0 (Async)
- **AI Gateway**: OpenClaw (HTTP API)
- **Database**: SQLite (local logs), ChromaDB (RAG - Deprecated/External)
- **Environment**: macOS (Native)

---

## 🚨 CRITICAL ARCHITECTURE RULES (v7.6+)

1.  **Do NOT implement local scraping/browser logic.**
    - Use `src.core.openclaw_client.OpenClawClient`.
    - Method: `await openclaw.invoke_tool("web_search", ...)`

2.  **Do NOT use `google.generativeai` (Old SDK).**
    - Use `google.genai` (New SDK) if direct Gemini access is needed.
    - Prefer OpenClaw where possible.

3.  **Do NOT use `WebScout` class.**
    - It is deprecated and kept only for reference.

---

## Quick Start

```bash
# 1. Activate venv
source .venv/bin/activate

# 2. Run Smoke Tests
python tests/smoke_test.py

# 3. Start Bot
./start_krab.command
```

---

## Project Structure

```
src/
├── core/
│   ├── openclaw_client.py   # <--- MAIN AI GATEWAY
│   ├── model_manager.py     # Router (Cloud/Local)
│   ├── tool_handler.py      # Tool execution
│   └── ...
├── handlers/
│   ├── commands.py          # User commands (!help, !status)
│   └── tools.py             # Research tools (!news, !scout)
├── utils/                   # Helpers
└── main.py                  # Entry point
```

---

## Code Sections

### OpenClaw Integration (`src/core/openclaw_client.py`)
This is the **primary** way to interact with the outside world (Search, RAG, News).
```python
# Example Usage
result = await openclaw.execute_agent_task("Research quantum physics")
```

### Command Handlers (`src/handlers/*.py`)
- use `@app.on_message(filters.me & ...)`
- Always handle errors with `try/except` and log them.

---

## Environment Variables (`.env`)

Required for v7.6+:
```ini
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_NAME=...
OPENCLAW_BASE_URL=http://localhost:8000
OPENCLAW_API_KEY=sk-...
GEMINI_API_KEY=...
```

---

## Testing

```bash
# Run all tests
python -m unittest discover tests

# Run specific test
python tests/test_openclaw_client.py
```

---

**Last Updated**: 2026-02-12 (Phase 4.1 Completed)
**Architect**: Antigravity
