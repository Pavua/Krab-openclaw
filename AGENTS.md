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

## 🚨 CRITICAL ARCHITECTURE RULES (v8.0+)

1.  **Do NOT implement local scraping/browser logic.**
    - Use `src.core.openclaw_client.OpenClawClient`.
    - Method: `await openclaw.invoke_tool("web_search", ...)`

2.  **Project Handover Engine**:
    - Every Finished project must trigger `src/core/handover.py` logic.
    - Check `src/core/agent_loop.py` for implementation details.

3.  **Thin Client Philosophy**:
    - Krab handles Telegram + Local state.
    - OpenClaw handles Tools + Reasoning.

---

## Quick Start

```bash
# 1. Activate venv
source .venv/bin/activate

# 2. Run Verification
./verify_project.command

# 3. Start Dashboard
streamlit run src/utils/dashboard_app.py
```

---

## Project Structure

```text
src/
├── core/
│   ├── openclaw_client.py   # <--- MAIN AI GATEWAY
│   ├── handover.py          # <--- Handover Engine (Phase 16.2)
│   ├── agent_loop.py        # <--- Autonomous Loop
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

### Handover Engine (`src/core/handover.py`)

Generates `HANDOVER.md` automatically at the end of project execution.

### Command Handlers (`src/handlers/*.py`)

- use `@app.on_message(filters.me & ...)`
- Always handle errors with `try/except` and log them.

---

## Environment Variables (`.env`)

Required for v8.0+:

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
pytest -q

# Run specific test
python tests/test_openclaw_client.py

# Run Handover Verification
python3 verify_handover.py
```

---

**Last Updated**: 2026-02-13 (Phase 12 Completed)
**Architect**: Antigravity (v8.0 Final)

---

> [!IMPORTANT]
> **Migration Readiness**: This repository is 100% prepared for migration to a new chat environment. See `MIGRATION.md` for onboarding.
