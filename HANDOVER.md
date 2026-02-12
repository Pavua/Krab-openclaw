# 🦀 Krab Project Handover (v7.6 Architecture Pivot)

**Project:** Krab AI Userbot
**Current Status:** ✅ Phase 4.1 Completed (Architecture Shift: Thin Client + OpenClaw Gateway)
**Date:** 12.02.2026
**Version:** v7.6 (OpenClaw Integration)

---

## 🚨 CRITICAL INSTRUCTIONS FOR NEXT AGENT/DEV

**STOP! READ THIS FIRST.**
This project has just undergone a MAJOR architectural refactoring. **Do not rollback changes.**

### 1. Architecture Paradigm Shift
- **Krab is now a THIN CLIENT.**
- **OpenClaw is the BRAIN.**
- **DO NOT** re-implement local `WebScout`, `RAGEngine`, or `Browser` logic in Python.
- **ALWAYS** use `src.core.openclaw_client.OpenClawClient` to delegate these tasks to the OpenClaw Gateway.

### 2. Core Components (Updated)
| Component | Status | Description |
|-----------|--------|-------------|
| `src/main.py` | **Updated** | Initializes `OpenClawClient`. No longer inits `WebScout`. |
| `src/core/openclaw_client.py` | **NEW** | **The most important file.** HTTP Client for OpenClaw API. |
| `src/handlers/tools.py` | **Updated** | `!news`, `!research` now call `OpenClawClient`. |
| `src/utils/web_scout.py` | **DEPRECATED** | Do not use. Kept for reference only. |
| `src/core/rag_engine.py` | **DEPRECATED** | Do not use. Use OpenClaw Vector Store. |

### 3. How to Run
#### Start the Bot (Userbot)
```bash
# Activate venv first!
source .venv/bin/activate
# Run the startup script
./start_krab.command
```
*Note: Requires active OpenClaw Gateway running separately.*

#### Verify Connectivity
```bash
# Run the connection test script
./.venv/bin/python tests/test_openclaw_client.py
```

### 4. Configuration (`.env`)
Ensure these variables are set in `.env`:
```ini
OPENCLAW_BASE_URL=http://localhost:8000  # Or your gateway URL
OPENCLAW_API_KEY=sk-...                  # Your OpenClaw API Key
```

---

## ✅ Recent Achievements (Phase 4.1)
1.  **Unified Client:** Created `OpenClawClient` to abstract all interactions with the AI Gateway.
2.  **Tool Migration:** Moved `web_search` and `news_digest` logic to OpenClaw.
3.  **Cleanup:** Decoupled monolithic local engines (`WebScout`, `RAGEngine`) from the main bot loop.
4.  **Verification:** Validated via `tests/test_openclaw_client.py` (Health, Tools, Agent Tasks).

## 🔮 Next Steps (Roadmap)
*See `task.md` for the full 20-phase roadmap.*

**Immediate Priorities (Phase 4.2+):**
1.  **Deep Integration**: Move more tools (e.g., specific scrapers) to OpenClaw.
2.  **Voice/Vision**: Ensure `!voice` and `!see` leverage OpenClaw's multimodal capabilities where possible.
3.  **Dockerization**: Prepare `docker-compose.yml` for the entire stack (Krab + OpenClaw).

## 📂 Key Documentation Artifacts
1.  **`task.md`**: The Master Roadmap. **Update this** as you progress.
2.  **`walkthrough.md`**: Proof of work and verification logs.
3.  **`AGENTS.md`**: High-level behavioral instructions.

---
**Signed off by:** Antigravity (Phase 4.1 Architect)
**System Integrity:** Stable
**Ready for:** Migration / Next Developer
