# Krab Architecture v2 — Skeleton (3 Artifacts)

> **Статус:** Design skeleton для Claude Design / Canva генерации. **v2.1** after @callme_chado feedback.
> **Источник v1:** `docs/ARCHITECTURE.md` (Session 16, 2026-04-20).
> **Feedback:** 8 пунктов Krab (Telegram critique) + Chado additions. Authored: 2026-04-21.

## Chado additions (v2.1 refinements)

Cross-artifact invariants **сильнее** чем сингловые улучшения:

1. **`shape=kind` инвариант** across all 3 artifacts — actor / process / store / external / schedule должны ВЫГЛЯДЕТЬ одинаково везде. Reader не переучивается между Hero/Engineering/Ops.
2. **Failure layer = toggle overlay**, не inline с happy path. Basic view = happy path only. Failure paths активируются через toggle / click. Glyph noise иначе съедает основу.
3. **Source-of-truth badge ТОЛЬКО на stores** (archive.db, models.json, runtime_state/). Процессы = trust-throughput, не truth-carriers — убрать TRUTH с services.
4. **Arrow legend MAX 3 типа**: `data` (cyan solid) / `control` (purple dashed) / `failure` (red, toggle overlay). Remove async green + bidirectional double + ambient grey — глаз не тянет 6 типов.
5. **Shared visual key для всех 3 артефактов** — одна легенда печатается ОДИН раз (в углу Hero + linked из Engineering/Ops). Обеспечивает consistency.

Updated arrow palette:

| Type | Style | Meaning |
|------|-------|---------|
| **data** | Cyan solid, filled arrowhead | Sync call, async event, data flow (unified!) |
| **control** | Purple dashed, diamond end | Policy, ACL, toggle, schedule signal |
| **failure** | Red solid (**overlay only**) | Fallback, error path, alert trigger |

Shape invariant (across Hero/Engineering/Ops):

| Kind | Shape |
|------|-------|
| Actor (human) | Circle avatar |
| Process (service) | Flat rectangle |
| Store (data) | Cylinder — **only here SoT badge** |
| External (cloud) | Hexagon |
| Schedule (cron/routine) | Clock/calendar icon |
>
> Три независимых артефакта с разной аудиторией и форматом.
> Общие константы (цвета, shapes, легенда стрелок) определены один раз в разделе «Shared Design System» — артефакты к нему ссылаются.

---

## Shared Design System

### Color Palette

| Role | Hex | Name |
|------|-----|------|
| Primary background | `#0D1117` | Navy midnight |
| Krab repo boundary | `#1A2840` | Deep ocean |
| OpenClaw runtime boundary | `#1A3028` | Dark pine |
| External services boundary | `#2A1A2E` | Dark plum |
| Telegram surface boundary | `#1A2550` | Midnight blue |
| Owner UI boundary | `#2A2010` | Dark amber |
| Accent cyan (primary flow) | `#00D4FF` | Electric cyan |
| Accent green (async) | `#00FF88` | Neon green |
| Accent red (error/failure) | `#FF4444` | Alert red |
| Accent purple (control plane) | `#9B59FF` | Control violet |
| Accent gold (source-of-truth) | `#FFD700` | Truth gold |
| Text primary | `#E6EDF3` | Off-white |
| Text secondary | `#8B949E` | Muted grey |

### Semantic Shape Table

| Shape | Meaning | Examples |
|-------|---------|---------|
| Rounded rectangle | UI surface / human touchpoint | Telegram clients, Owner panel browser, Owner @p0lrd avatar |
| Rectangle (flat) | Python / Node.js service | userbot_bridge.py, web_app.py, OpenClaw Gateway |
| Cylinder | Persistent data store | archive.db, krab_runtime_state/, models.json |
| Hexagon | External cloud provider | Gemini API, Anthropic API, OpenAI API |
| Diamond | Decision / router | LLM Router, ACL check, Model fallback selector |
| Parallelogram | Queue / async channel | MTProto queue, MCP SSE stream, SwarmBus |
| Dashed rectangle | Control / policy layer | ACL, silence_mode, chat_filter_config |
| Person icon (avatar) | Human actor | Owner @p0lrd, Telegram contacts |
| Document | Spec / config file | .env, models.json, CLAUDE.md |

### Arrow Legend

| Style | Color | Meaning | Example |
|-------|-------|---------|---------|
| Solid, filled arrowhead | Cyan `#00D4FF` | Synchronous request / HTTP call | userbot → openclaw_client → Gateway |
| Dashed, open arrowhead | Green `#00FF88` | Async event / fire-and-forget | SwarmBus delegation, proactive_watch alerts |
| Double-headed solid | Cyan `#00D4FF` | Bidirectional / persistent session | MTProto session, SSE stream |
| Solid, filled arrowhead | Red `#FF4444` | Error path / fallback trigger | Cloud failure → LM Studio fallback |
| Dashed, diamond end | Purple `#9B59FF` | Control plane signal | silence_mode toggle, ACL policy enforce |
| Dotted, no arrowhead | Grey `#8B949E` | Ambient / low-signal dependency | config.py reads (omit if non-narrative) |

Arrow pruning rule: omit any arrow whose removal does not change the narrative. Config reads, internal imports, and logging sinks are ambient — do not draw them unless they are a critical path.

### Source-of-Truth Badges

Applied as small corner labels on data stores and config nodes:

| Badge | Color | Meaning | Applied to |
|-------|-------|---------|-----------|
| TRUTH | Gold `#FFD700` | Authoritative source — runtime reads from here | `~/.openclaw/agents/main/agent/models.json`, `archive.db` |
| CACHE | Blue `#4488FF` | Derived, TTL-bound, can be regenerated | `history_cache`, `chat_ban_cache`, `chat_capability_cache` |
| DERIVED | Teal `#44BBAA` | Computed from TRUTH, eventually consistent | `krab_llm_route_ok` metric, `model_status` endpoint |
| OBSERVED | Orange `#FF8800` | Runtime sampled, not persisted between restarts | Prometheus gauge values, in-memory ChatWindow buffers |
| STALE-PRONE | Red outline | Known to drift — needs manual reconcile | `CLAUDE.md` (may lag runtime), `next_session.md` handoff |

### Ownership Boundary Colors (tinted region fills, 15% opacity)

| Boundary | Fill color | Contents |
|----------|-----------|---------|
| Krab repo (`src/`) | `#1A2840` (navy) | All Python runtime modules |
| `~/.openclaw` runtime | `#1A3028` (pine) | Gateway process, models.json, krab_runtime_state/ |
| External services | `#2A1A2E` (plum) | Gemini, Claude, OpenAI, Brave Search |
| Telegram network | `#1A2550` (midnight blue) | MTProto endpoints, Swarm accounts, contacts |
| Owner UI | `#2A2010` (amber) | Owner panel :8080, MCP servers :8011-8013 |
| Shared / macOS host | `#1A1A1A` (dark grey) | launchd LaunchAgents, Hammerspoon, LM Studio |

---

## Artifact 1: Hero / Vision

### Purpose
Вау-эффект для README cover, GitHub social preview, onboarding newcomers. Никаких технических деталей — только концептуальная карта с wow-ощущением от масштаба.

### Format
- Size: `1920 × 1080 px` (landscape 16:9), also export `A3 landscape` for print
- Canva design_type: `poster` (or `presentation` template if poster unavailable)
- Color budget: 3 accent colors (cyan, green, purple) + navy background
- Font: single typeface family, 2 weights only (regular + bold)
- Text density: max 3 words per block label, 1 sentence subtitle per block

### Block List (5-7 blocks, horizontal pipeline)

| # | Block name | Subtitle | Shape | Position |
|---|-----------|---------|-------|---------|
| 1 | Telegram Surface | MTProto · pyrofork · 175+ commands | Rounded rect, Telegram blue | Top-left |
| 2 | Owner @p0lrd | Root operator · commands & prompts | Avatar circle | Far left center |
| 3 | Krab Brain | Python 3.13 · uvloop · single-process | Rectangle, accent cyan border | Center |
| 4 | OpenClaw Gateway | Node.js 20 · LLM Router · :18789 | Rectangle, accent green border | Center-right |
| 5 | Memory Vault | 43k msgs · archive.db · model2vec | Cylinder, gold | Bottom-left |
| 6 | Multi-Agent Swarm | 4 teams · TaskBoard · Kanban | Parallelogram, purple | Bottom-center |
| 7 | Owner Panel | :8080 · 204 APIs · Dashboards | Rounded rect, amber | Right |

### Canva Design Brief (< 500 chars, copy-paste ready)

```
Dark tech poster, 1920x1080, navy #0D1117 bg. 7 blocks as nodes in horizontal pipeline: Telegram Surface (blue rounded rect) → Krab Brain (cyan rect, center, largest) → OpenClaw Gateway (green rect) → Owner Panel (amber rect, right). Below center: Memory Vault (gold cylinder), Multi-Agent Swarm (purple hexagon). Arrows: cyan solid left-to-right. Font: Inter bold labels, regular subtitles. No decorative elements. Max 3 words per label. GitHub cover vibe.
```

---

## Artifact 2: Engineering / Runtime

### Purpose
Технический blueprint для разработчиков — полный компонентный граф, request flow, failure paths, API contracts, ownership boundaries, source-of-truth markers. Reference документ при code review и onboarding.

### Format
- Size: `2400 × 1200 px` wide format (can scroll horizontally in browser)
- Claude Design design_type: `html` (read-only artifact, interactive hover details)
- Layout: top-down, 5 swimlane rows, ownership boundaries as color-tinted background regions
- Arrow density: narrative-only (pruned per shared legend)

### Component Tree (complete, hierarchical)

- **LAYER 0** — Human Actors (top bar): Owner @p0lrd (owner ACL), Telegram contacts, Swarm accounts
- **LAYER 1** — Telegram Surface (midnight blue boundary): MTProto pyrofork 2.3.69, Forum Topics (Swarm Krab -1003703978531), Swarm DM accounts (@yung_nagato, @hard2boof, @opiodimeo, @p0lrdp_AI)
- **LAYER 2** — Krab Python Runtime (navy TRUTH boundary):
  - `userbot_bridge.py` (entry, uvloop, dispatcher, chat_window_manager, message_batcher, semaphore=3)
  - `handlers/command_handlers.py` (154 handlers, ACL, !swarm, !translator, !costs, +150)
  - `openclaw_client.py` (HTTP bridge, SSE stream, tool loop, fallback)
  - `mcp_client.py` (tool manifest, call_tool_unified)
  - `core/*` (proactive_watch, inbox_service, cost_analytics, swarm_bus, swarm_task_board, memory_indexer_worker, scheduler, cron_native_scheduler, silence_mode, spam_filter, observability)
  - `modules/web_app.py` (FastAPI :8080, 204 endpoints, /metrics, dashboards)
- **LAYER 3** — OpenClaw Gateway (pine green boundary, TRUTH for routing):
  - Gateway process Node.js 20 :18789
  - models.json TRUTH
  - LLM Router: primary gemini-3-pro-preview, fallbacks (2.5-pro → 2.5-flash → 3-flash), local LM Studio
  - Tool adapters (Telegram, Gmail, Notion, Figma, Linear, Browser Playwright, Voice, Files, Canva, Sentry)
  - krab_runtime_state/ OBSERVED
- **LAYER 4** — MCP Servers (amber owner UI boundary): mcp-yung-nagato :8011, mcp-p0lrd :8012, mcp-hammerspoon :8013
- **LAYER 5** — Data & External (plum boundary): archive.db TRUTH, ~/.openclaw/krab_memory/, Gemini API, Anthropic API, OpenAI API, Brave Search

### Request Flow — Happy Path (cyan arrows)

```
Owner "!ask what is the weather"
  → MTProto (Telegram network)
  → userbot_bridge.py (Pyrogram event)
  → message_priority_dispatcher (P0: command)
  → chat_window_manager
  → command_handlers.py (!ask)
  → ACL check (owner → pass)
  → openclaw_client.py
  → Gateway :18789 (POST /chat)
  → LLM Router → Gemini API (HTTPS streamed)
  ← SSE chunks → reply assembly
  → Telegram send_message → Owner reads
```

### Failure Logic Layer

**Primary → Fallback chain** (red arrows with labels):

- Gemini 429 → gemini-2.5-pro-preview → gemini-2.5-flash → LM Studio → degraded "AI unavailable"
- Gateway down → openclaw_client: RouterError → safe_reply → proactive_watch → InboxItem + Telegram alert
- archive.db locked → memory_indexer: log + skip → /metrics stale (graceful degradation)
- MCP server down → mcp_client: tool unavailable → LLM text-only

**Survival matrix:**

| Scenario | Degraded | Survival |
|---------|----------|---------|
| All clouds down | No AI | LM Studio local (if loaded) |
| LM Studio unloaded | No local | Degraded reply sent |
| Gateway down | No LLM/tools | Non-AI commands still work (!stats, !health) |
| archive.db locked | No recall | Commands work, recall empty |
| All MCP down | No tools | LLM text-only |
| Telegram FloodWait | Delayed delivery | Rate limiter queues + retry |

### Source-of-Truth Markers

| Data | Badge | Location |
|------|-------|---------|
| Active model | TRUTH | `~/.openclaw/agents/main/agent/models.json` |
| Message history | TRUTH | `archive.db` |
| Runtime state | OBSERVED | `~/.openclaw/krab_runtime_state/` |
| Swarm memory | CACHE | `~/.openclaw/krab_runtime_state/swarm_memory_*.json` |
| LLM route health | DERIVED | `krab_llm_route_ok` Prometheus gauge |
| CLAUDE.md | STALE-PRONE | Repo root — may lag runtime |
| Cost budget | CACHE | `COST_MONTHLY_BUDGET_USD` env + accumulator |

### API Contracts (key inter-component paths)

| From | To | Contract |
|------|----|---------|
| userbot_bridge | openclaw_client | POST JSON to `:18789/chat` |
| openclaw_client | Gateway | SSE stream (tool_call events) |
| mcp_client | MCP servers | SSE on `:8011-8013/sse` |
| web_app | external monitors | Prometheus on `:8080/metrics` |
| proactive_watch | inbox_service | Python function call |
| proactive_watch | Telegram | MTProto `send_message(owner_id, ...)` |
| swarm_bus | swarm accounts | MTProto DM |
| swarm_channels | Forum group | MTProto topics (`message_thread_id`) |

### Claude Design Brief (< 500 chars, copy-paste ready)

```
Technical architecture diagram, 2400x1200, dark navy bg #0D1117. 5 horizontal swimlanes, color-tinted ownership regions (navy=Krab, pine=OpenClaw, plum=External, amber=Owner UI, midnight=Telegram). Semantic shapes per legend: services=flat rects, data=cylinders, routers=diamonds, queues=parallelograms. Cyan solid arrows=sync calls, red solid=failure/fallback, green dashed=async events, purple dashed=control signals. Gold badges on TRUTH stores. Failure paths explicitly labeled. Inter Sans font. No decorative elements.
```

---

## Artifact 3: Ops / Truth

### Purpose
Operations runbook в визуальном формате — что мониторить, куда смотреть при инциденте, как выглядит здоровый vs нездоровый стейт. Первый экран on-call инженера.

### Format
- Size: `2000 × 1200 px` dashboard style
- Claude Design design_type: `dashboard` (or `html` with grid layout)
- Layout: 3 columns, 4 rows grid (12 panels total)
- Color coding: green=healthy, amber=warning, red=critical, grey=unknown/stale

### Dashboard Panels (12)

| # | Panel | Key content |
|---|-------|-------------|
| P1 | System Health | Krab process, Gateway :18789, Panel :8080, MCP :8011-13 |
| P2 | LLM Route | Primary, fallback chain, local, last latency |
| P3 | Cost Runway | Budget $/month, runway days, top provider |
| P4 | Archive DB | Messages count, size, FTS5, model2vec |
| P5 | Memory Indexer | Embed queue depth, last embed, chunks TRUTH |
| P6 | Swarm Status | Tasks open, rounds today, active team OBSERVED |
| P7 | Alert Paths | KrabDown→Telegram, LLMRouteDown→DM, ArchiveGrowth, InjectionSpike |
| P8 | Cron Routines | health-watcher 15m, ear-watcher 15m, backend-log 4h, daily-maint 02:07 |
| P9 | Inbox | Open N, Processing, Stale (!), Last update |
| P10 | State Files | models.json TRUTH, krab_runtime_state/, archive.db TRUTH, swarm_memory/ CACHE |
| P11 | Recovery Map | Gateway down step1, Cloud fail step2, DB locked step3, MCP down step4 |
| P12 | Timeline | Last 24h events (red=errors, amber=warnings, green=healthy) |

### Health Indicators

| Indicator | Healthy | Warning | Critical | Source |
|-----------|---------|---------|---------|-------|
| Krab process | Running, MTProto connected | FloodWait > 60s | Process down | `GET /api/health` |
| OpenClaw Gateway | :18789 responding | Latency > 5s | Connection refused | `GET /api/openclaw/report` |
| LLM primary route | `krab_llm_route_ok=1` | Fallback active | All routes failed | `GET /metrics` |
| Archive DB | Size < 500 MB, no lock | Size > 500 MB | Locked / corrupt | `krab_archive_db_size_bytes` |
| Memory indexer | Embed queue < 100 | Queue > 1000 | Worker crashed | `GET /api/memory/indexer` |
| Cost budget | < 80% | 80–95% | > 95% | `GET /api/costs/budget` |
| MCP servers | All 3 up | 1 down | 2+ down | `GET /api/system/diagnostics` |
| Swarm | Tasks < 20 open | Tasks > 50 | Listener crashed | `GET /api/swarm/stats` |

### Alert Paths (red arrows, explicit)

- **KrabDown** → launchd KeepAlive restarts → Telegram alert @p0lrd (if available) → InboxItem critical
- **KrabLLMRouteDown** (2 min) → openclaw_client fallback chain → proactive_watch DM → InboxItem high
- **KrabArchiveGrowingFast** (>10k msgs/h) → InboxItem info (manual prune via `/api/ops/maintenance/prune`)
- **KrabMemoryValidatorOverload** (>15 pending, 10min) → InboxItem warning + DM
- **KrabMetricsStale** (>2 min) → Prometheus external fires (Krab cannot self-report)

### Scheduled Routines

| Label | LaunchAgent | Interval | Action |
|-------|------------|---------|-------|
| health-watcher | `ai.krab.health-watcher` | 15 min | `/api/health` + alert if degraded |
| ear-watcher | `ai.krab.ear-watcher` | 15 min | KrabEar STT check |
| backend-log-scanner | `ai.krab.backend-log-scanner` | 4 h | Scan logs, ErrorDigest |
| daily-maintenance | `ai.krab.daily-maintenance` | 02:07 daily | Archive vacuum, cache prune |
| leak-monitor | `ai.krab.leak-monitor` | KeepAlive | Memory leak watchdog |
| monthly-arch-refresh | Desktop Routine | 1st of month | Update Canva export PNG |

### State Persistence Locations

| Data | Path | Badge |
|------|------|-------|
| Message archive | `~/.openclaw/krab_memory/archive.db` | TRUTH |
| Model routing | `~/.openclaw/agents/main/agent/models.json` | TRUTH |
| Runtime state | `~/.openclaw/krab_runtime_state/` | OBSERVED |
| Swarm memory | `~/.openclaw/krab_runtime_state/swarm_memory_*.json` | CACHE |
| Swarm channels | `~/.openclaw/krab_runtime_state/swarm_channels.json` | CACHE |
| Task board | `~/.openclaw/krab_runtime_state/swarm_task_board.json` | CACHE |
| Reminders | `~/.openclaw/krab_runtime_state/reminders.json` | CACHE |
| Inbox items | `~/.openclaw/krab_runtime_state/inbox_items.json` | OBSERVED |

### Recovery Protocols (on-call decision tree)

**Krab not responding in Telegram:**
1. Check process running? NO → `new start_krab.command`. YES → logs for FloodWait/exception loop
2. FloodWait: wait out (do NOT SIGKILL)
3. Exception loop → check openclaw_client errors → Gateway may be down
4. Gateway down → `openclaw gateway` (NOT SIGHUP)
5. Verify: `!health` в Telegram

**All AI responses fail:**
1. Check `/api/openclaw/cron/status` → LLM route status
2. Gemini quota: `curl :18789/health`
3. Cloud quota → `openclaw models set google/gemini-2.5-flash`
4. All cloud fail → load LM Studio (ONE AT A TIME, 36 GB limit)

**archive.db locked:**
1. STOP Krab (wait full stop)
2. `sqlite3 ~/.openclaw/krab_memory/archive.db "PRAGMA integrity_check;"`
3. If corrupt: restore backup or accept loss
4. Restart

**MCP server down:**
1. `launchctl list | grep com.krab.mcp`
2. `launchctl kickstart -k gui/$UID/com.krab.mcp-yung-nagato` (or p0lrd/hammerspoon)
3. Verify: `curl :8011/sse`

### Claude Design Brief (< 500 chars, copy-paste ready)

```
Ops dashboard, 2000x1200, dark navy bg #0D1117. 12-panel grid (3 cols × 4 rows). Each panel: dark card #1A2030 with title, status dots (green/amber/red), key metrics. Top row: system health, LLM route, cost runway. Middle rows: DB, indexer, swarm, alerts, cron, inbox. Bottom row: state files, recovery map, timeline sparkline. Font: Inter mono for values, Inter regular for labels. No decorations. Grafana-style aesthetic.
```

---

## Implementation Notes

### Generation order
1. **Hero (Canva)**: fastest, unblock README cover image
2. **Engineering (Claude Design HTML)**: most value for dev team
3. **Ops (Claude Design dashboard)**: needed before next on-call rotation

### Callout / Appendix pattern (feedback #7)
Components with > 5 sub-items get floating callout card (details panel) on hover in HTML artifact. Keeps main canvas uncluttered. Never put sub-module names on main canvas arrow paths — label only primary component.

### Arrow pruning checklist (feedback #8)
For each arrow, ask:
- Does removing it break reader's understanding?
- If no: remove.
- Config reads, logging sinks, internal imports: always remove.
- Tool execution loop: keep (core contract).
- Fallback chain: keep all red arrows (narrative).

### Minimum viable v2
Ship **Artifact 2 (Engineering)** alone — it covers full runtime picture and directly addresses all 8 feedback points. Hero and Ops can follow.

---

*Skeleton generated: 2026-04-21. Next action: feed each brief to Claude Design / Canva.*
*Reference: `docs/ARCHITECTURE.md` (v1 blueprint).*
