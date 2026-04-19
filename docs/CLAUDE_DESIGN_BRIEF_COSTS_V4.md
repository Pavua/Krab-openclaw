# Claude Design Brief: Krab Dashboard V4 — /costs Page

> Ready-to-use brief. Paste directly into Claude Design (claude.ai/design).
> Deliverable: single self-contained HTML file.
> Date: 2026-04-19. Owner: pavelr7@gmail.com.

---

## 1. Цель / Goal

Страница `/costs` в Krab Owner Panel (`http://127.0.0.1:8080`) — финансовая аналитика
AI-расходов для одного владельца (single-user, no public auth).

Показывает: сколько потрачено сегодня/за месяц, по каким моделям, через какие каналы,
каков runway (сколько дней до исчерпания кредитов), FinOps метрики (tool calls, fallbacks,
context tokens), прогресс бюджета, тренды.

Аудитория: только owner. Язык UI: bilingual (Russian + English, mix — как в остальных
страницах Krab). Доступ через sidebar nav Krab Panel.

---

## 2. Data Sources

Все endpoints на `http://127.0.0.1:8080`. Polling: 30s. Fallback: mock data встроен
в HTML (см. секцию 8).

### 2.1 GET /api/costs/report — основной
```json
{
  "ok": true,
  "report": {
    "total_cost_usd": 0.004476,
    "total_calls": 1,
    "budget_monthly_usd": 50.0,
    "budget_remaining_usd": 49.995524,
    "budget_used_pct": 0.01,
    "by_model": {
      "codex-cli/gpt-5.4": {
        "input_tokens": 59613, "output_tokens": 17,
        "cost_usd": 0.004476, "calls": 1
      }
    },
    "period_start": "2026-04-01T00:00:00Z",
    "period_end": "2026-04-19T23:50:47Z",
    "input_tokens": 59613,
    "output_tokens": 17,
    "total_tool_calls": 0,
    "total_fallbacks": 0,
    "total_context_tokens": 0,
    "avg_context_tokens": 0,
    "by_channel": { "telegram": 1 }
  }
}
```
Response path: `data.report`. Fallback если HTTP != 200 или поле отсутствует.

### 2.2 GET /api/costs/budget
```json
{
  "ok": true,
  "budget": {
    "monthly_limit_usd": null,
    "spent_usd": 0.004476,
    "remaining_usd": null,
    "budget_ok": true,
    "used_pct": null,
    "forecast_calls": 1.5
  }
}
```
ВАЖНО: `monthly_limit_usd` может быть `null` — бюджет не установлен. UI показывает
"Not set" + кнопку "Set Budget". Response path: `data.budget`.

### 2.3 GET /api/ops/runway
```json
{
  "ok": true,
  "runway": {
    "status": "ok",
    "credits_usd": 300.0,
    "reserve_ratio": 0.1,
    "spendable_budget_usd": 270.0,
    "monthly_cost_usd": 0.004476,
    "daily_burn_usd": 0.000149,
    "runway_days": 1809651.47,
    "horizon_days": 80,
    "horizon_ok": true,
    "safe_calls_per_day": 754,
    "avg_cost_per_call_usd": 0.004476
  }
}
```
Response path: `data.runway`. Если `runway_days > 3650`, показывать "∞ (safe)".
Если `runway_days < 30` — красный алерт. Если `runway_days < 90` — жёлтый.

### 2.4 GET /api/costs/history
```json
{
  "ok": true,
  "total_records": 1,
  "history": [
    {
      "model_id": "codex-cli/gpt-5.4",
      "input_tokens": 59613,
      "output_tokens": 17,
      "cost_usd": 0.004476,
      "timestamp": 1776633162.08,
      "channel": "telegram",
      "is_fallback": false,
      "tool_calls_count": 0
    }
  ]
}
```
Response path: `data.history` (array). Используется для trend line chart.

### 2.5 GET /api/ops/cost-report
```json
{
  "ok": true,
  "report": {
    "status": "ok",
    "usage": { "input_tokens": 59613, "output_tokens": 17, "tracked_calls": 1 },
    "costs": {
      "session_usd": 0.004476, "month_usd": 0.004476,
      "monthly_budget_usd": null, "budget_ok": true
    },
    "forecast": { "monthly_calls_forecast": 1.5 },
    "by_model": { "codex-cli/gpt-5.4": { "cost_usd": 0.004476, "calls": 1 } }
  }
}
```
Secondary source для reconciliation.

### 2.6 POST /api/costs/budget — update budget
Request body: `{ "monthly_limit_usd": 50.00 }`.
Ожидаемый ответ: `{ "ok": true }`. После успеха — refetch budget.

---

## 3. Layout Requirements

**Тёмный фон:** `#0a0a1a`. Liquid-glass карточки. Responsive (mobile-first). Sidebar — 220px.

### Карточка 1 — Budget Progress (top, full-width)
- Прогресс-бар (green → amber → red при pct < 50/80/100)
- Spent / Limit / Remaining
- Если `monthly_limit_usd == null`: серый бар + текст "Budget not set" + кнопка inline
- Кнопка "Set Budget" → modal с input `monthly_limit_usd`

### Карточка 2 — Stats Row (Total Cost / Total Calls / Total Tokens)
- Sparkline mini-chart (7 дней из history)
- Trend indicator (↑↓ % от предыдущего дня)
- Skeleton loading animation

### Карточка 3 — Provider Breakdown (donut + legend)
- `by_model` данные из /api/costs/report
- По клику на provider — фильтрует таблицу истории
- Цвета: cyan для gpt/openai, purple для claude/anthropic, green для local/llama

### Карточка 4 — Runway Estimate
- Большая цифра: "N days" (или "∞ safe" если > 3650)
- Sub-metrics: daily burn, credits remaining, safe calls/day
- Алерт-badge: RED если < 30 дней, AMBER если < 90 дней, GREEN если ok

### Карточка 5 — FinOps Breakdown (2x2 grid)
- Tool Calls total, Fallbacks (warning badge если > 0),
  Avg Context Tokens, Cost per Request
- By Channel — horizontal bar chart

### Карточка 6 — Trend Line (full-width bottom)
- Line chart: cost_usd по дням из `/api/costs/history`
- Toggle: Cost / Calls / Tokens
- Fallback если history пустая: flat line с mock

### Карточка 7 — History Table (collapsible)
- Колонки: Timestamp, Model, Channel, Cost, Tokens, Fallback
- Пагинация (20 записей), Export CSV

---

## 4. Tech Stack

- **Язык:** Vanilla JS + fetch API (НЕ React/Vue)
- **CSS:** Inline styles + CSS variables
- **Charts:** Chart.js v4 via CDN
- **Fonts:** system-ui + 'Outfit' from Google Fonts
- **Theme:** Dark only
- **CSS Variables:**
  ```
  --glass-bg: rgba(255,255,255,0.06)
  --glass-border: rgba(255,255,255,0.10)
  --accent-cyan: #7dd3fc
  --accent-purple: #a78bfa
  --accent-green: #34d399
  --accent-red: #f87171
  --accent-yellow: #fbbf24
  --bg-primary: #0a0a1a
  --radius-card: 20px
  ```
- **Responsive:** grid auto-fit minmax(300px, 1fr), mobile tab bar

---

## 5. Interactions

1. **Edit Budget Inline** — modal с input, POST /api/costs/budget, toast
2. **Export CSV** — client-side из history array, `krab_costs_YYYY-MM-DD.csv`
3. **Export JSON** — full report+budget+runway snapshot
4. **Filter by Provider** — клик на legend → фильтр таблицы
5. **Date Range Filter** — два date input, client-side filter
6. **Auto-refresh** — 30s polling, countdown в nav, статус-dot
7. **Runway Alert Banner** — red if <30d, amber if <90d, dismissible

---

## 6. Constraints

- Server: FastAPI, same-origin, no CORS issues
- Single-file HTML, no build, no npm
- Auth: опциональный header `X-Krab-Web-Key` (const TOKEN = '' вверху)
- Bilingual RU+EN (EN headers, RU badges/toasts)
- Target size < 100KB

---

## 7. Acceptance Criteria

1. **Runway alert** — <30d → red banner, >90d → no banner
2. **Budget null state** — "Not set" + set button → modal → save → bar turns green
3. **Provider breakdown** — click legend → table filtered → removable badge
4. **Export** — valid CSV opens in Excel/Numbers
5. **API offline** — mock fallback, status-dot red, toast
6. **Cost trend** — toggle Cost/Calls/Tokens re-renders chart

---

## 8. Built-in Mock Data

```javascript
const MOCK_REPORT = {
  total_cost_usd: 42.15, total_calls: 1254,
  budget_monthly_usd: 50.0, budget_remaining_usd: 7.85, budget_used_pct: 84.3,
  input_tokens: 850000, output_tokens: 210000,
  total_tool_calls: 342, total_fallbacks: 12,
  total_context_tokens: 1060000, avg_context_tokens: 845,
  by_model: {
    "gpt-4o": { cost_usd: 28.50, calls: 800 },
    "claude-3-5-sonnet": { cost_usd: 12.20, calls: 350 },
    "llama-3-70b": { cost_usd: 1.45, calls: 104 }
  },
  by_channel: { "telegram": 850, "translator": 200, "swarm": 154, "api": 50 }
};
const MOCK_BUDGET = { monthly_limit_usd: 50.0, spent_usd: 42.15, remaining_usd: 7.85, used_pct: 84.3 };
const MOCK_RUNWAY = { credits_usd: 300.0, daily_burn_usd: 1.40, runway_days: 192, safe_calls_per_day: 754 };
const MOCK_HISTORY = [
  { date: "2026-04-13", cost_usd: 5.5, calls: 150 },
  { date: "2026-04-14", cost_usd: 6.2, calls: 180 },
  { date: "2026-04-15", cost_usd: 4.8, calls: 140 },
  { date: "2026-04-16", cost_usd: 7.1, calls: 210 },
  { date: "2026-04-17", cost_usd: 6.5, calls: 190 },
  { date: "2026-04-18", cost_usd: 8.4, calls: 240 },
  { date: "2026-04-19", cost_usd: 3.7, calls: 144 }
];
```

---

## 9. Deliverable

**Один HTML файл:** `src/web/prototypes/costs_v4_claude_design.html`

- Self-contained (CSS+JS inline, CDN only for Chart.js)
- Работает при открытии через `http://127.0.0.1:8080/prototypes/costs_v4_claude_design.html`
- Nav bar как в других v4 страницах
- Все 6 API endpoints через fetch с fallback на MOCK
- Polling 30s, countdown в nav

**Checklist перед сдачей:**
- [ ] Все 6 user stories работают на mock данных
- [ ] Budget null state без JS errors
- [ ] Runway > 3650 → "∞ (safe)"
- [ ] CSV export валиден
- [ ] Responsive 375px без горизонтального скролла
- [ ] Charts рендерятся

---

## 10. Reference Files (в репозитории)

- `src/web/v4/costs.html` — существующий V4 costs (скопировать nav)
- `src/web/v4/liquid-glass.css` — design tokens
- `src/web/v4/index.html` — sidebar nav
- `src/modules/web_app_costs_dashboard.py` — legacy backend
- `docs/DASHBOARD_COSTS_UPDATE_SPEC.md` — FinOps fields spec
- `docs/DASHBOARD_REDESIGN_SPEC.md` — V4 design guide
