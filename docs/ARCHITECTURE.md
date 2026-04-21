# Krab — System Architecture

> Generated via **Claude Design** (Session 16, 2026-04-20).
> Single-source-of-truth visual blueprint.

## Live URLs

### v2 (2026-04-21) — 3 artifacts per Krab+Chado critique
Skeleton: `docs/ARCHITECTURE_V2_SKELETON.md`

**Artifact 1 — Hero / Vision (Canva)**
- Variant 4 (primary): https://www.canva.com/d/wDX_xg3mClWE0t7
- Variant 1 (alt): https://www.canva.com/d/rZnjP7YLE8JzKaY

**Artifact 2 — Engineering / Runtime (Claude Design)**
- Live: https://claude.ai/design/p/f8108663-9376-444f-8c2c-1e93302a02d6?file=Krab+Architecture.html
- Features: 5 swimlanes (SURFACE/RUNTIME/GATEWAY/TOOLS/DATA), toggle overlays (fallback paths, control signals, animate data flow), shape invariant (cylinders = stores with TRUTH badges only), 3 arrow types max.

**Artifact 3 — Ops / Truth (Claude Design)**
- Live (Claude Design): https://claude.ai/design/p/90d6069e-1ccf-4144-863d-db54f3c08685?file=Ops+Dashboard.html
- Canva mirror (editable): https://www.canva.com/d/3dkWS667S3h08UB
- Features: 12 панелей на 2000×1200 canvas (System Health, LLM Route, Cost Runway, Archive DB, Memory Indexer, Swarm Status, Alert Paths, Cron Routines, Inbox, State Files, Recovery Map, 24H Timeline). Реальные данные (752,184 msgs / 417 MB / 19 chats, $142/$500 budget, swarm-042 active team). Tweaks toggles: red alert overlay (P7), density, pulse status dots. Print-ready variant — `Ops Dashboard-print.html`.

### v1 (2026-04-20 Session 16)
- Claude Design (edit): https://claude.ai/design/p/11567838-b049-4ab7-b217-83103fe5ec68?file=Krab+Architecture.html
- Canva (editable export): https://www.canva.com/d/22tGJQlMa2UbXyi

## Preview

![Krab Architecture](artifacts/architecture_2026-04.png)

*Preview будет обновлён через `krab-openclaw-monthly-arch` Desktop Routine 1-го числа каждого месяца.*

## Structure — 4 horizontal layers

### Layer 01 — User Interface · client surface · human-in-the-loop

- **Telegram**: clients · updates · encrypted channels (MTProto · pyrofork 2.3.59)
- **Owner @p0lrd**: root, drives commands & prompts
- **Swarm accounts**: @yung_nagato (ops), traders (fin), coders, analysts
- Annotation: *gated by semaphore budget=3*

### Layer 02 — Krab Core · python runtime · dispatcher & scheduler

- **Krab Bridge** (python 3.12.4 · single-process · uvloop · panel :8080)
- Commands (registry · prefix · alias · acl) — **175+**
- Message Dispatcher (handlers · routing · rate-limit) — async
- Background Tasks (loops · cron · debouncers) — **11 live**
- Memory Indexer (model2vec · embed · recall) — **43k msg**
- State metrics: Queue 3/3, Handlers 42 bound, Tasks 11 loops, http://localhost:8080/panel
- **Wave 4 semaphore budget = 3**

### Layer 03 — OpenClaw Gateway · node.js service · tool adapters · LLM router

- **Gateway** (host :18789, node 20.x LTS)
- Integrations: Telegram, Gmail, Notion, Figma, Linear, Browser (playwright), Voice (whisper · tts), Files, **Canva, Sentry**
- **LLM Router**: codex-cli (ready), Gemini 3 Pro (fallback chain), LM Studio

### Layer 04 — Data & Providers · persistence · ICP · external clouds

- **Archive DB** (SQLite + FTS5 + model2vec, 43k msgs, 51 MB)
- **3 MCP servers**: yung-nagato :8011, p0lrd :8012, hammerspoon :8013
- **External clouds**: Gemini, Claude, OpenAI

## Cross-cutting (right side panel)

### Sentry · errors
- ERR MTProto FloodWait · retry
- WARN LLM Router fallback → LM Studio
- ERR Gmail oauth token refresh
- INF Archive vacuum completed

### Linear · tasks
- KRB-214 Wave 4 semaphore (AGE-5 in our actual Linear)
- KRB-213 Memory indexer
- KRB-212 OpenClaw Figma port
- KRB-211 Archive FTS5 snippet
- KRB-210 Session 16 commit scan

### launchd · 5 routines
- `ai.krab.leak-monitor` KeepAlive
- `ai.krab.health-watcher` 15 min
- `ai.krab.ear-watcher` 15 min
- `ai.krab.backend-log-scanner` 4 h
- `ai.krab.daily-maintenance` 02:07

## Use cases

- Cover image для README (подключить когда export в PNG)
- Onboarding doc для новых контрибьюторов
- Linear project description attachment
- Sentry issue template context ("слой 3, компонент OpenClaw Gateway")
- Session recap hero image
- GitHub social preview (OpenGraph)

## A/B test result (Canva vs Claude Design)

Первая генерация Session 16. Brief: `docs/design_briefs/architecture_diagram.md`.

| Tool | Output quality | Speed | Best для |
|------|---------------|-------|----------|
| **Canva** | 4 candidates, infographic style, generic | ~30 sec | Session recap cards, social posts |
| **Claude Design** | Single polished technical blueprint, real data representation | ~3 min | **Technical architecture, dashboards, complex UI** |

**Winner для technical diagrams**: Claude Design 🏆.
Canva оставляем для quick session recaps.

## Refresh protocol

Каждое 1-е число месяца Desktop routine `krab-openclaw-monthly-arch` обновит данные
в Canva export (`22tGJQlMa2UbXyi`) с текущими метриками:
- Total commits
- API endpoints count
- Archive DB size + msg count
- Last Session number

Export PNG → commit в `docs/artifacts/architecture_YYYY-MM.png`.
