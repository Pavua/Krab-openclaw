# Dashboard V4 — Session 10 Features Frontend Spec

**Author:** Krab Session 11
**For:** Gemini 3.1 Pro (frontend implementation)
**Date:** 2026-04-17
**Base:** `src/web/v4/index.html` Liquid Glass design (Session 9)

---

## Goal

Add Session 10 features visibility в существующий V4 Hub. 9 новых компонентов в layout-grid.
Агрегирующий endpoint `/api/session10/summary` уже merged (Wave 6) — backend changes NOT needed.

## Backend endpoints ready (NO backend changes needed)

| Endpoint                                  | Returns                               | Used by component  |
|-------------------------------------------|---------------------------------------|--------------------|
| `GET /api/session10/summary`              | Aggregated session 10 stats           | All cards (single poll) |
| `GET /api/ecosystem/health`               | Full health + `session_10` block      | Detail modal       |
| `GET /api/memory/indexer`                 | Indexer queue + processed             | Memory card detail |
| `GET /api/memory/search?q=X&mode=hybrid`  | Search results (hits[])               | Search box inline  |
| `GET /api/chrome/dedicated/status`        | Chrome dedicated state                | Chrome card detail |
| `POST /api/chrome/dedicated/launch`       | Launch result                         | Chrome button      |
| `GET /api/commands`                       | 145+ commands with metadata           | Commands gallery   |
| `POST /api/model/switch`                  | Model switch result                   | Model selector     |
| `GET /api/openclaw/cloud`                 | Provider health                       | Provider dots      |
| `GET /api/ops/timeline`                   | Recent runtime events (optional)      | Debug panel        |

## Components

### 1. Memory Validator Card

**Position:** Hub sidebar, 1/3 width
**Props from `/api/session10/summary`:**
- `memory_validator.safe_total`
- `memory_validator.injection_blocked_total`
- `memory_validator.confirmed_total`
- `memory_validator.pending_count`
- `memory_validator.enabled` (bool)

**Visual:**
```
╭─────────────────────────╮
│ 🛡️ Memory Validator     │
│                         │
│ Safe:      1,247        │
│ Blocked:   3            │
│ Pending:   2   ⚠️       │
│ Confirmed: 1            │
│                         │
│ [View pending] button   │
╰─────────────────────────╯
```

- If `!enabled` — show grayscale header + "disabled" ribbon
- If `pending_count > 0` — red dot (`--accent-red`) + "View pending" button opens modal
- Numbers formatted with thin-space thousands (Intl.NumberFormat `ru`)

### 2. Memory Archive Card

**Props:**
- `memory_archive.message_count` (formatted with spaces — "42 708")
- `memory_archive.size_mb`
- `memory_archive.chunks_count`
- `memory_archive.chats_count`
- `memory_archive.indexer_state` ("running" | "idle" | "error")

**Visual:**
```
╭─────────────────────────╮
│ 🧠 Memory Archive       │
│                         │
│ 42 708 messages         │
│ 9 099 chunks · 27 chats │
│ 49.14 MB                │
│                         │
│ Indexer: ● running      │
│                         │
│ [Search memory] input   │
│ ↓ inline results (top 3)│
╰─────────────────────────╯
```

- Inline search input — onEnter → `GET /api/memory/search?q=X&mode=hybrid`
- Show top 3 hits inline с chat_id / date / snippet
- Indexer dot: green=running, yellow=idle, red=error
- Click card → modal с детализацией и `/api/memory/indexer` queue

### 3. Dedicated Chrome Card

**Props:**
- `dedicated_chrome.enabled`
- `dedicated_chrome.running`
- `dedicated_chrome.port` (default 9222)

**Visual:**
- Green pill если `running`
- Red pill если `enabled && !running` (attention)
- Gray pill если `!enabled` (feature off)
- Button [Launch] если `enabled && !running` — POST `/api/chrome/dedicated/launch`
- Link-icon ссылка `http://127.0.0.1:{port}` если running

### 4. Auto-Restart Status Card

**Props:**
- `auto_restart.enabled`
- `auto_restart.services_tracked` — array of service names
- `auto_restart.total_attempts_last_hour`

**Visual:**
- Traffic-light (●) по каждому сервису (stacked rows): green=healthy, yellow=restarting, red=down
- Attempt counter — badge rendered яркой красной pill если `total_attempts_last_hour > 0`
- Empty state — "No services tracked" если list пустой

### 5. Commands Gallery (full-width section)

**Props:** `/api/commands` → 145+ commands с полями `{name, category, description, usage, owner_only}`
**Filter tabs:** All / Basic / Memory / Swarm / Voice / System / Translator
**Search box** — filter by `name` substring (debounced 200ms)
**Click on card** — modal с `usage` + `description` + `owner_only` badge

Visual:
- Grid `repeat(auto-fill, minmax(220px, 1fr))` — glass-card mini-tiles
- Name в `<code>` monospace, category pill сверху справа
- Hover → `--glass-bg-hover` + subtle scale 1.02
- `IntersectionObserver` для lazy-load (145 cards не рендерить сразу)

### 6. Active Route Banner (top of Hub)

**Props from `/api/ecosystem/health`:**
- `ecosystem.last_runtime_route.provider`
- `ecosystem.last_runtime_route.model`
- `ecosystem.last_runtime_route.status` ("ok" | "degraded" | "failed")

**Visual:** sticky banner под nav:
```
[●] codex-cli / gpt-5.4     [Switch ▾] button
```

- Dot colored by status
- Click Switch → dropdown fallback models → POST `/api/model/switch` с `{model: "..."}`
- Toast confirmation сверху экрана

### 7. Session 10 Stats Ribbon

**Props:** `session_info.new_tests_count`, `session_info.commits_count`, `session_info.name`, `session_info.date`
**Visual:** inline ticker рядом с hub-header:
```
Session 10 · 2026-04-17 — 155 new tests · 48 commits
```

Small badges `glass-card` padding 0.5rem.

### 8. Correlation ID Debug Panel (hidden by default)

**Props:** `/api/ops/timeline` → recent requests с `request_id`, `endpoint`, `latency_ms`
**Toggle:** tap-count (5 taps на session info) ИЛИ query-string `?debug=1`
**Visual:** floating panel bottom-right, `<details>` native collapse
- Only if `observability.correlation_id_active === true`

### 9. Observability Ribbon

**Props:**
- `observability.correlation_id_active`
- `observability.tool_indicator_enabled`
- `observability.stagnation_threshold_sec`

**Visual:** small feature-flag badges (glass-mini):
- `[CID ✓]` if `correlation_id_active`
- `[Tools ✓]` if `tool_indicator_enabled`
- `[Stall: 120s]` stagnation threshold
- Tooltip on hover объясняет каждый флаг

## Design constraints

- **Liquid Glass** — существующая тема. Semi-transparent white cards на gradient background, `backdrop-filter: blur(40px) saturate(180%)`
- **Reuse CSS variables** из `liquid-glass.css`: `--glass-bg`, `--glass-border`, `--accent-cyan`, `--accent-red`, `--radius-card`
- **No external JS libs** — vanilla JS, без React/Vue. Pattern как в existing V4 страницах
- **Responsive** — grid collapses to 1 column на `<768px`
- **Loading states** — skeleton (pulsing `--glass-bg`) пока данные loading
- **Error states** — если endpoint 500/timeout — "⚠️ Data unavailable (retry in 10s)" в карточке
- **Number formatting** — `Intl.NumberFormat('ru-RU').format(n)` для thousands (тонкий пробел)
- **Accessibility** — `role="status"` на refresh areas, `aria-live="polite"`

## Fetching strategy

**ОДИН poll каждые 10 сек** — `GET /api/session10/summary` (1 запрос для всех 9 карточек).
Полный refresh по [↻] button в hub-header. НЕ polling per-card (overhead + CPU).

Exceptions (on-demand):
- Memory search — onEnter
- Model switch — onClick
- Chrome launch — onClick
- Commands gallery — один раз при mount (cache в-memory)

```js
const POLL_INTERVAL = 10_000;
let summaryTimer = null;

async function refreshSummary() {
  try {
    const r = await fetch('/api/session10/summary');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderAllCards(data);
  } catch (e) {
    renderErrorState(e.message);
  }
}

function startPolling() {
  refreshSummary();
  summaryTimer = setInterval(refreshSummary, POLL_INTERVAL);
}

// pause polling when tab hidden
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearInterval(summaryTimer);
  else startPolling();
});
```

## File layout

Add to `src/web/v4/`:
- `session10.html` — standalone страница `/v4/session10` (registered в FastAPI `web_app.py`)
- `session10.js` (~150-200 lines) — fetch + render components
- `session10.css` — component-specific styles (extends liquid-glass.css)

Альтернатива: embed как secton в `index.html` под existing hub (если user хочет единую страницу).

## Implementation tips для Gemini

1. Use `<template>` tags + `.cloneNode(true)` для component instances.
2. `IntersectionObserver` для lazy-load Commands Gallery (145 cards).
3. `<details>` native collapse для advanced sections (Debug Panel).
4. ARIA labels обязательно — `aria-label`, `role="region"`, `aria-live="polite"` on refresh areas.
5. Dark mode — наследуй existing variable scheme (`--bg-primary`, `--bg-surface`, `--accent-*`).
6. Icons — SVG inline (не emoji для "critical" signals). Emoji OK для заголовков карточек.
7. `Intl.NumberFormat('ru-RU')` для thousands-spacing ("42 708" вместо "42,708").
8. Debounce search input 200ms (`setTimeout` + `clearTimeout`).
9. Error toasts — 1 shared container, auto-dismiss 5s.
10. Mobile — `@media (max-width: 768px)` — grid → flex column, touch-friendly 44px targets.

---

## Sample response `GET /api/session10/summary`

Запрос:
```bash
curl -s http://127.0.0.1:8080/api/session10/summary | python3 -m json.tool
```

Response (actual live, 2026-04-17):
```json
{
    "ok": true,
    "generated_at": 1776444545,
    "session_info": {
        "name": "Session 10",
        "date": "2026-04-17",
        "status": "closed",
        "new_tests_count": 155,
        "commits_count": 48
    },
    "memory_validator": {
        "enabled": false,
        "safe_total": 0,
        "injection_blocked_total": 0,
        "confirmed_total": 0,
        "confirm_failed_total": 0,
        "pending_count": 0
    },
    "memory_archive": {
        "exists": true,
        "size_bytes": 51527680,
        "size_mb": 49.14,
        "message_count": 43080,
        "chats_count": 27,
        "chunks_count": 9131,
        "indexer_state": "running"
    },
    "new_commands": [
        {"name": "!confirm", "description": "Подтвердить persistent memory write (owner)"},
        {"name": "!reset", "description": "Aggressive очистка 4 слоёв истории"},
        {"name": "!memory stats", "description": "Memory Layer статистика"}
    ],
    "dedicated_chrome": {
        "enabled": false,
        "running": false,
        "port": 9222
    },
    "auto_restart": {
        "enabled": false,
        "services_tracked": [],
        "total_attempts_last_hour": 0
    },
    "observability": {
        "correlation_id_active": true,
        "tool_indicator_enabled": true,
        "stagnation_threshold_sec": 120
    },
    "known_issues": []
}
```

Notes:
- `enabled: false` на validator / dedicated_chrome / auto_restart — feature-flag state, UI должна gracefully показать "disabled" badge (не error)
- `size_mb` already rounded 2 decimals на backend — рендерить as-is
- `new_commands` — 3 команды в этой сессии; Commands Gallery (компонент 5) берёт полный список из `/api/commands`
- `known_issues` — если непустой массив → banner сверху с предупреждением

---

## Deliverable для user → Gemini 3.1 Pro

Attach при запуске Gemini:
1. Этот spec (`DASHBOARD_V4_SESSION10_FRONTEND_SPEC.md`)
2. `src/web/v4/index.html` — для style/layout reference
3. `src/web/v4/liquid-glass.css` — CSS token dictionary
4. Sample JSON response выше (уже embedded)

Expected output от Gemini:
- `src/web/v4/session10.html`
- `src/web/v4/session10.js`
- `src/web/v4/session10.css`

После генерации — user передаст файлы обратно агенту Krab для review + route registration в `web_app.py`.
