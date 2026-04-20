# Brief: Krab Architecture Diagram

**Project**: krab_userbot
**Design type**: infographic
**Target**: `docs/ARCHITECTURE.png` — cover for main README / handbook

## Description (paste into tool)

High-fidelity software architecture diagram for "Krab" — personal Telegram userbot (Python + pyrofork + asyncio). Dark navy #0a0a1a background, cyan #7dd3fc / green #34d399 / purple #a78bfa accents. Flat modern line icons, glass morphism cards.

4 horizontal layers top→bottom:

**L1 User Interface**: Telegram app → @p0lrd owner + 4 swarm accounts (@yung_nagato, traders, coders, analysts). Label "MTProto / pyrofork".

**L2 Krab Userbot Core**: central node "Krab bridge" (Python PID, :8080). Branches: message dispatcher, 175+ commands, background tasks, memory indexer. 4 swarm team nodes. Link "Owner Panel :8080".

**L3 OpenClaw Gateway**: gateway box (Node.js, :18789). 15 integrations as ports: Telegram, Slack, Discord (disabled), WhatsApp (disabled), Gmail, Notion, Figma, Canva, Sentry, Linear, Browser, Voice. AI Router: codex-cli, Gemini 3 Pro, LM Studio local.

**L4 Data & Providers**: Archive DB (SQLite+FTS5+model2vec, 43k msgs, 51MB). 3 MCP servers: yung-nagato:8011, p0lrd:8012, hammerspoon:8013. External: Gemini, Claude, OpenAI APIs.

**Cross-cutting (right side)**: Sentry (errors), Linear (tasks), 5 launchd routines.

**Arrows**: solid cyan = request flow, dashed green = async background, red = error path.

**Annotations**: "Wave 4: Semaphore budget=3", "Session 16: 18 commits", "Memory Phase 2: incremental embeddings".

## Context
- Style: dark navy, cyan/green, liquid-glass cards
- Language: EN labels (technical audience)
- Use case: embed in README.md, docs/ARCHITECTURE.md, presentations
- Print-safe at 2x resolution

## Status
- Canva candidates 1-4 generated ✅ (saved 1 and 3 as editable)
  - https://www.canva.com/d/cGi0rgCQvENmKkc
  - https://www.canva.com/d/ximzOhF8CY0FUY7
- Claude Design: pending (after parallel Ear session завершится)
