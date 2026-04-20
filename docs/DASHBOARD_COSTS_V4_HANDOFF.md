# Handoff Spec: Krab Dashboard V4 — /costs Page

**Version:** 1.0 (2026-04-20)
**Source of truth:** `src/web/prototypes/costs_v4_claude_design.html` (lines 1–1131)
**CSS tokens:** `src/web/v4/liquid-glass.css`
**Brief:** `docs/CLAUDE_DESIGN_BRIEF_COSTS_V4.md`

---

## Overview

`/costs` — страница финансовой аналитики AI-расходов в Krab Owner Panel (`http://127.0.0.1:8080/v4/costs`). Single-user (owner only), без публичной авторизации. Показывает: текущий бюджет, runway (сколько дней до исчерпания кредитов), разбивку по провайдерам/каналам/моделям, FinOps-метрики, тренд-граф, историю вызовов. Polling 30 секунд, таймер в nav. Fallback на встроенные mock-данные при недоступности API.

**Аудитория:** только owner. Язык UI: EN заголовки + RU бейджи/тосты.

---

## Layout

### Общий контейнер

```css
.container { max-width: 1200px; margin: 0 auto; padding: 2rem 1rem; }
body { padding-bottom: 80px; } /* место под мобильный tab bar */
```

Порядок секций сверху вниз (desktop и mobile):

1. Nav bar (sticky, 56px)
2. Alert Banner (Runway) — скрыт если runway >= 90 дней
3. Page title row + Export buttons
4. Budget Card (full-width)
5. Stats Row — 3 карточки в grid (`grid-3`)
6. Runway Card (full-width)
7. Provider + FinOps row — 2 колонки (`grid-2`)
   - Левая: Provider Donut
   - Правая: FinOps 2x2 + By Channel stacked
8. Usage by Model Table (full-width)
9. Trend Line Chart (full-width)
10. History Table + Date Filter (full-width)
11. Footer (last updated + next refresh)
12. Budget Modal (overlay, z-index 100)
13. Toast container (fixed top-right, z-index 999)

### Grid классы

| Класс | Columns | Gap |
|-------|---------|-----|
| `.grid-2` | `repeat(auto-fit, minmax(300px, 1fr))` | `1rem` |
| `.grid-3` | `repeat(auto-fit, minmax(250px, 1fr))` | `1rem` |
| `.grid-2x2` | `1fr 1fr` | `0.75rem` |

---

## Design Tokens

Все токены из `src/web/v4/liquid-glass.css` (строки 6–28):

### Тёмная тема (default)

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-primary` | `#0a0a1a` | Фон body |
| `--bg-surface` | `#111118` | Поверхность элементов |
| `--glass-bg` | `rgba(255,255,255,0.06)` | Фон карточек и кнопок |
| `--glass-bg-hover` | `rgba(255,255,255,0.10)` | Hover фон |
| `--glass-border` | `rgba(255,255,255,0.10)` | Граница карточек/таблиц |
| `--glass-blur` | `40px` | backdrop-filter blur |
| `--glass-specular` | `inset 0 0.5px 0 rgba(255,255,255,0.12)` | Верхний блик |
| `--glass-shadow` | `0 8px 40px rgba(0,0,0,0.25)` | Тень карточки |
| `--accent-cyan` | `#7dd3fc` | Основной акцент, заголовки, nav active |
| `--accent-purple` | `#a78bfa` | Вторичный акцент |
| `--accent-green` | `#34d399` | Успех, OK-состояния |
| `--accent-red` | `#f87171` | Ошибки, критические состояния |
| `--accent-yellow` | `#fbbf24` | Предупреждения |
| `--radius-card` | `20px` | Скругление карточек |
| `--radius-button` | `12px` | Скругление кнопок |
| `--radius-input` | `14px` | Скругление инпутов |
| `--font-primary` | `-apple-system, BlinkMacSystemFont, 'Outfit', sans-serif` | Основной шрифт |

### Светлая тема (data-theme="light")

| Token | Dark value | Light override |
|-------|-----------|----------------|
| `--bg-primary` | `#0a0a1a` | `#f5f7fb` |
| `--glass-bg` | `rgba(255,255,255,0.06)` | `rgba(255,255,255,0.60)` |
| `--accent-cyan` | `#7dd3fc` | `#0284c7` |
| `--accent-red` | `#f87171` | `#dc2626` |

### Текстовые утилиты

| Класс | Цвет (dark) |
|-------|------------|
| `.text-accent` | `#bae6fd` |
| `.text-muted` | `rgba(255,255,255,0.72)` |
| `.text-success` | `#6ee7b7` |
| `.text-error` | `#fca5a5` |
| `.text-warning` | `#fde68a` |
| `.text-purple` | `#c4b5fd` |

### Body background

Три radial-gradient поверх `--bg-primary`:
- Cyan blob: `circle at 15% 50%`, `rgba(125,211,252,0.15)`, `35%`
- Purple blob: `circle at 85% 30%`, `rgba(167,139,250,0.15)`, `35%`
- Green blob: `circle at 50% 80%`, `rgba(52,211,153,0.05)`, `40%`

`background-attachment: fixed` — блобы не скроллятся.

---

## Components

### 1. Nav Bar (`.glass-nav`)

**Refs:** HTML lines 95–121

| Prop | Value |
|------|-------|
| Height | 56px |
| Position | sticky, top: 0 |
| z-index | 100 |
| Backdrop | blur(40px) saturate(180%) |
| Border-bottom | 1px solid `--glass-border` |

**Дочерние элементы:**
- `.nav-logo` — "Krab AI", `--accent-cyan`, font-weight 700, 1.25rem
- `.nav-links` — горизонтальный flex, gap 2rem
- `.nav-link` — цвет `rgba(255,255,255,0.6)` inactive; `#ffffff` active/hover
- `.nav-link.active::after` — 2px cyan underline с box-shadow `0 -2px 8px rgba(125,211,252,0.5)`
- Status dot (`#api-status`) — 10x10px, `border-radius: 50%`, green/yellow/red
- Countdown `#nav-timer` — "↻ Xs", `.text-muted .text-sm`
- Bell wrapper + `.bell-count` badge (скрыт если 0)

**Nav links:** Hub / Chat / **Costs** (active) / Inbox / Swarm / Translator / Ops / Settings / Commands

---

### 2. Runway Alert Banner (`.runway-alert-banner`)

**Refs:** HTML lines 133–136, CSS lines 55–59

| Variant | Condition | Color | Border |
|---------|-----------|-------|--------|
| `.red` | runway_days < 30 | `rgba(248,113,113,0.12)` | `rgba(248,113,113,0.4)` |
| `.amber` | 30 <= runway_days < 90 | `rgba(251,191,36,0.10)` | `rgba(251,191,36,0.35)` |
| hidden | runway_days >= 90 | — | — |

**Структура:**
```html
<div class="runway-alert-banner [red|amber] visible">
  <span id="runway-banner-text">...</span>
  <button class="banner-close">×</button>
</div>
```

- Добавляется класс `.visible` (display: flex) через JS при рендере runway
- Dismiss: клик на `×` → `banner.classList.remove('visible')` (только на текущую сессию)
- border-radius: 14px; padding: 0.875rem 1.25rem

---

### 3. Budget Card (`.glass-card`, full-width)

**Refs:** HTML lines 139–158

**Layout:** вертикальный flex внутри glass-card, padding 1.5rem.

**Состояния:**

| State | Condition | Behavior |
|-------|-----------|----------|
| `null` | `monthly_limit_usd == null` | Серый прогресс-бар (width: 0%), текст "Бюджет не установлен — используется кредит провайдера" |
| `loading` | До первого ответа API | `.skeleton` на `#budget-spent`, `#budget-remaining`, `#budget-limit` |
| `set` | `monthly_limit_usd > 0` | Цветной прогресс-бар по `used_pct` |
| `warning` | `used_pct >= 50` | Прогресс-бар amber `--accent-yellow` |
| `critical` | `used_pct >= 80` | Прогресс-бар red `--accent-red` |
| `ok` | `used_pct < 50` | Прогресс-бар green `--accent-green` |

**Progress bar:**
```css
.progress-track { background: rgba(0,0,0,0.3); border-radius: 999px; height: 12px; border: 1px solid var(--glass-border); }
.progress-fill { height: 100%; transition: width 0.5s ease, background-color 0.5s ease; border-radius: 999px; }
```

**Логика цвета fill (JS):**
```javascript
fill.style.background = pct >= 80 ? 'var(--accent-red)'
  : pct >= 50 ? 'var(--accent-yellow)'
  : 'var(--accent-green)';
fill.style.width = Math.min(pct, 100) + '%';
```

**Нижняя строка:**
- Левая: "Потрачено" / `$XX.XX` (`.text-xl`, color `.text-success`)
- Правая: "Остаток (Limit $XX)" / `$XX.XX` или "Not set" (`.text-muted`)

**Кнопка "Set Budget":** `glass-button`, правый верхний угол карточки. Открывает `#budget-modal`.

---

### 4. Stat Cards (`.stat-card`, 3 штуки в `.grid-3`)

**Refs:** HTML lines 161–186

| Card | ID | Color class | Metric |
|------|----|-------------|--------|
| Total Cost | `#total-cost` | `.text-success` (`#6ee7b7`) | `total_cost_usd` |
| Total Calls | `#total-calls` | `.text-accent` (`#bae6fd`) | `total_calls` |
| Total Tokens | `#total-tokens` | `.text-purple` (`#c4b5fd`) | `input_tokens + output_tokens` |

**Каждая карточка содержит:**
1. Header: label (`.text-muted .text-sm`) + trend indicator (скрыт если нет данных за 2 дня)
2. Большое значение (`.text-3xl`, font-size 1.875rem, font-weight 700)
3. Sparkline wrapper (`#sparkline-{cost|calls|tokens}`, 36px высота, 120px ширина)

**Trend indicator (`.trend-indicator`):**
- `↑ X.X%` или `↓ X.X%` — сравнение последних двух дней из history
- Цвет: рост cost → red (`#fca5a5`), снижение cost → green (`#6ee7b7`); инвертировано для calls/tokens
- `padding: 0.125rem 0.375rem; border-radius: 4px; background: rgba(255,255,255,0.1)`

**Sparkline:** SVG polyline, inline, 7 дней из `MOCK_HISTORY_RAW` / реальной history. Цвета: cost `#6ee7b7`, calls `#7dd3fc`, tokens `#a78bfa`.

**Hover эффект stat-card:**
```css
.stat-card:hover { transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
```

**Count-up animation:** при первой загрузке (`isFirstLoad = true`) — `animateValue()` с ease `1 - (1-p)^4`, duration 1200ms.

**Loading state:** `.skeleton` класс на `.text-3xl` элементе — shimmer `rgba(255,255,255,0.08)→0.14→0.08`, `background-size: 200%`, animation 2s linear.

---

### 5. Runway Card (`.glass-card`, full-width)

**Refs:** HTML lines 189–213

**Большое значение (`.runway-big`):**
- font-size: 3rem; font-weight: 800; letter-spacing: -0.03em

**4 состояния:**

| State | Condition | Display text | Color | Badge |
|-------|-----------|-------------|-------|-------|
| `ok` | runway_days >= 90 | N (число) или "∞ (safe)" если > 3650 | `#34d399` | `.runway-badge.green` "OK" |
| `warning` | 30 <= runway_days < 90 | N days | `#fbbf24` | `.runway-badge.amber` "ВНИМАНИЕ" |
| `critical` | runway_days < 30 | N days | `#f87171` | `.runway-badge.red` "КРИТИЧНО" |
| `n/a` | runway_days == null / NaN | "—" | `var(--text-muted)` | `.runway-badge.green` "N/A" |
| `no-spend` | runway_days == 0 И monthly_cost == 0 | "∞" | `#34d399` | `.runway-badge.green` "БЕЗ РАСХОДОВ" |

**Badge CSS:**
```css
.runway-badge.green  { background: rgba(52,211,153,0.15);  color:#6ee7b7; border:1px solid rgba(52,211,153,0.3); }
.runway-badge.amber  { background: rgba(251,191,36,0.15);  color:#fde68a; border:1px solid rgba(251,191,36,0.3); }
.runway-badge.red    { background: rgba(248,113,113,0.15); color:#fca5a5; border:1px solid rgba(248,113,113,0.3); }
```

**Sub-metrics (`.runway-sub`, 3-column grid):**

| ID | Label | Source field |
|----|-------|-------------|
| `#runway-burn` | "Daily Burn" | `runway.daily_burn_usd` → `fmtUSD6()` |
| `#runway-credits` | "Credits" | `runway.credits_usd` → `fmtUSD()` |
| `#runway-safe-calls` | "Safe calls/day" | `runway.safe_calls_per_day` → `fmtNum()` |

`.runway-sub-item`: `text-align: center; padding: 0.75rem; background: rgba(0,0,0,0.15); border-radius: 12px; border: 1px solid var(--glass-border)`.

---

### 6. Provider Donut Chart

**Refs:** HTML lines 219–237, JS lines 558–614

**Библиотека:** Chart.js v4 (`cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js`)

**Canvas:** `#donut-chart`, 180x180px (mobile: 150x150px). Тип `doughnut`, cutout `68%`.

**Легенда:** кастомная через JS (`.donut-legend`). Каждый `.legend-item`:
```css
.legend-item { display:flex; align-items:center; gap:0.6rem; cursor:pointer; padding:0.3rem 0.5rem; border-radius:8px; font-size:0.875rem; }
.legend-item:hover { background: rgba(255,255,255,0.05); }
.legend-item.active { background: rgba(255,255,255,0.08); }
```
- `.legend-dot` — 10x10px circle
- `.legend-name` — flex:1
- `.legend-cost` — `rgba(255,255,255,0.6)`, font-size 0.8rem

**Цвета провайдеров (функция `providerColor(name)`):**

| Паттерн (lowercase) | Цвет |
|--------------------|------|
| `gpt`, `openai`, `codex` | `#7dd3fc` (cyan) |
| `claude`, `anthropic`, `sonnet`, `opus` | `#a78bfa` (purple) |
| `llama`, `local`, `mistral`, `lm-studio` | `#34d399` (green) |
| `gemini`, `google` | `#fbbf24` (yellow) |
| all others | `#fb923c` (orange) |

**Filter badge (`.filter-badge`):**
```css
.filter-badge { background:rgba(125,211,252,0.12); border:1px solid rgba(125,211,252,0.3); color:#7dd3fc; padding:0.2rem 0.65rem; border-radius:999px; font-size:0.75rem; }
```
Показывается при активном фильтре. `×` — `#clear-provider-filter` → `clearProviderFilter()`.

**Empty state:** если `by_model` пуст → вместо canvas вставляется текст "Нет данных — сделай первый AI-запрос в Telegram", centered.

---

### 7. FinOps 2x2 Grid

**Refs:** HTML lines 241–261

4 мини-карточки (`.glass-card` внутри `.grid-2x2`, padding 0.75rem):

| ID | Label | Source field | Color |
|----|-------|-------------|-------|
| `#tool-calls` | "Tool Calls" | `total_tool_calls` | default |
| `#fallbacks` | "Fallbacks" | `total_fallbacks` | `.text-warning` (`#fde68a`) если > 0 |
| `#avg-context` | "Avg Context" | `avg_context_tokens` → `fmtNum()` + " tk" | default |
| `#cost-per-req` | "Cost/Request" | `total_cost_usd / total_calls` → `fmtUSD6()` | default |

Все четыре — `.skeleton` до первого fetch.

---

### 8. By Channel Horizontal Bars

**Refs:** HTML lines 262–268, JS lines 527–556

Контейнер `#channels-container`. Каждый `.bar-row` (CSS grid `100px 1fr 70px`, gap 0.75rem):
- `.bar-label` — название канала, `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`
- `.bar-container` — `background: rgba(0,0,0,0.3); border-radius: 999px; height: 8px`
- `.bar-fill` — `transition: width 0.5s ease`
- Значение справа — call count или cost (integer = `fmtNum`, float = `fmtUSD`)

**Палитра каналов:** `['#bae6fd','#c4b5fd','#6ee7b7','#fde68a','#fca5a5']` по очереди.
Максимум 8 каналов (сортировка по убыванию).

**Empty state:** div с `text-align: center; color: var(--text-muted); padding: 2rem; text: "Нет данных по каналам"`.

---

### 9. Usage by Model Table

**Refs:** HTML lines 273–297

**Колонки:**

| Header | Alignment | Source |
|--------|-----------|--------|
| Model | left | `model_id` из `by_model` |
| Cost (USD) | right | `cost_usd` → `fmtUSD()` |
| Calls | right | `calls` → `fmtNum()` |
| Tokens | right | `input_tokens + output_tokens` → `fmtNum()` |
| Avg Cost/Call | right | `cost_usd / calls` → `fmtUSD6()` |

**Стили таблицы:**
```css
th { color: #bae6fd; font-weight: 600; }
th, td { padding: 0.75rem; border-bottom: 1px solid var(--glass-border); }
tbody tr:hover { background: var(--glass-bg-hover); }
```

**Loading state:** одна строка со `.skeleton` в каждой ячейке.
**Empty state:** `<td colspan="5" class="text-center text-muted">Нет данных</td>`.

---

### 10. Trend Line Chart

**Refs:** HTML lines 299–312, JS lines 647–703

**Toggle (`.trend-toggle`):**
```css
.trend-toggle { background: rgba(0,0,0,0.25); border-radius: 20px; padding: 3px; border: 1px solid var(--glass-border); }
.trend-toggle button { color: rgba(255,255,255,0.5); font-size: 0.8rem; font-weight: 600; padding: 0.3rem 0.85rem; border-radius: 16px; }
.trend-toggle button.active { background: var(--glass-bg-hover); color: #fff; }
```
Три кнопки: `data-metric="cost"` (default active), `data-metric="calls"`, `data-metric="tokens"`.

**Размер canvas:** высота 180px (`.trend-chart-wrap`), responsive width.

**Цвета линий:**

| Metric | Color | Label |
|--------|-------|-------|
| cost | `#6ee7b7` | "Cost (USD)" |
| calls | `#7dd3fc` | "Calls" |
| tokens | `#a78bfa` | "Tokens" |

**Chart.js config:**
- `type: 'line'`, `fill: true` (area `color + '20'`), `tension: 0.4`, `borderWidth: 2`, `pointRadius: 3`
- X grid: `rgba(255,255,255,0.05)`, ticks: `rgba(255,255,255,0.5)`, max 7 labels
- Y ticks callback: cost → `'$' + v.toFixed(2)`, others → `fmtNum(v)`

**Data aggregation:** `groupByDate()` — группировка `histArr` по YYYY-MM-DD (Unix timestamp `row.timestamp * 1000` или `row.date`).

**Fallback:** если `rawHistory` пуст → использует `MOCK_HISTORY_RAW`.

---

### 11. History Table + Date Filter

**Refs:** HTML lines 314–356, JS lines 706–788

**Date filter row:**
```css
.date-filter { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
input[type="date"] { background: rgba(0,0,0,0.2); border: 1px solid var(--glass-border); color: #fff; padding: 0.4rem 0.75rem; border-radius: 10px; font-size: 0.8rem; }
```
Элементы: label "From" + `#date-from`, label "To" + `#date-to`, кнопка "Reset".

**Колонки:**

| Header | Source | Alignment |
|--------|--------|-----------|
| Timestamp | `new Date(row.timestamp * 1000).toLocaleString()` | left, `.text-sm .text-muted` |
| Model | `row.model_id` | left, `.text-sm` |
| Channel | `row.channel` | left, `.text-sm` |
| Cost | `fmtUSD6(row.cost_usd)` | right, `.text-success .text-sm` |
| Tokens | `fmtNum(input + output)` | right, `.text-muted .text-sm` |
| Fallback | "FALLBACK" / "—" | left; FALLBACK color `#fde68a`, font-size 0.75rem |

**Пагинация:** PAGE_SIZE = 20. Кнопки `#hist-prev` / `#hist-next` (`.glass-button .export-btn`).

**Info строка:** "N записей, стр. X/Y" (`#history-info`).

**Empty state:** `<td colspan="6" class="text-center text-muted">Нет данных</td>`.

**Loading state:** `<td colspan="6" class="text-center text-muted">Загрузка...</td>`.

---

### 12. Budget Modal

**Refs:** HTML lines 370–384

```css
.modal-overlay { position: fixed; top:0; left:0; right:0; bottom:0; background: rgba(0,0,0,0.6); backdrop-filter: blur(5px); z-index: 100; display: none; align-items: center; justify-content: center; }
.modal-overlay.active { display: flex; }
.modal-content { max-width: 400px; width: 90%; }
```

**Форма `#budget-form`:**
- `input[type="number"]` — `step="0.01"`, `min="0"`, `class="glass-input"`, placeholder "e.g. 50.00"
- "Cancel" button → `#btn-close-modal` → убирает `.active`
- "Save" button — `glass-button-primary`; submit → POST `/api/costs/budget` с body `{ monthly_limit_usd: value }`
  - success: toast "Бюджет сохранён", refetch budget, close modal
  - error: toast "Ошибка сохранения бюджета" (`.error`)

---

### 13. Toast System

**Refs:** HTML line 92, JS lines 438–449, CSS lines 62–65

```css
.toast-container { position: fixed; top: 70px; right: 1rem; z-index: 999; display: flex; flex-direction: column; gap: 0.5rem; }
.toast { background: rgba(20,20,41,0.95); border: 1px solid var(--glass-border); backdrop-filter: blur(20px); border-radius: 12px; padding: 0.75rem 1.25rem; min-width: 260px; max-width: 360px; animation: fadeUp 0.3s ease; }
.toast.success { border-color: rgba(52,211,153,0.4); }
.toast.error   { border-color: rgba(248,113,113,0.4); }
```
Auto-dismiss: 4000ms, fade-out `opacity → 0` за 400ms.

---

## States and Interactions

### Polling

| Element | Behavior |
|---------|----------|
| `#nav-timer` | Обратный отсчёт от 30 до 0, текст "↻ Xs" |
| `#footer-timer` | "Next refresh in Xs" |
| `#api-status` | green при успешном fetch, yellow при offline/mock |
| `#last-updated` | "Last updated: HH:MM:SS" после каждого успешного fetch |

Цикл: `setInterval(refresh, 1000)`, при `timer === 0` → вызов `fetchAll()`, reset `timer = 30`.

### fetchAll() — порядок запросов

Все запросы параллельно (`Promise.allSettled`):
1. `GET /api/costs/report` → `rawReport`
2. `GET /api/costs/budget` → budget data
3. `GET /api/ops/runway` → `rawRunway`
4. `GET /api/costs/history` → `rawHistory`

При HTTP != 200 или сетевой ошибке → использует MOCK, API-статус = yellow.

### Provider Filter

| Element | State | Behavior |
|---------|-------|----------|
| `.legend-item` | inactive | `background: transparent` |
| `.legend-item` | active (clicked) | `background: rgba(255,255,255,0.08)` |
| `#provider-filter-badge` | filter active | видим, показывает имя провайдера |
| `#provider-filter-badge .filter-badge-x` | — | клик → `clearProviderFilter()` |
| History table | filter active | показывает только строки с matching `model_id` |

Toggle: повторный клик на ту же легенду → снять фильтр.

### Export CSV

Функция `exportCSV()` — строит CSV из `historyAll` (все записи, без фильтра по дате/провайдеру):
- Заголовки: `Timestamp,Model,Channel,Cost USD,Input Tokens,Output Tokens,Fallback,Tool Calls`
- Filename: `krab_costs_YYYY-MM-DD.csv`
- Метод: `Blob` + `URL.createObjectURL` + временный `<a>`

### Export JSON

Функция `exportJSON()` — snapshot: `{ report: rawReport, runway: rawRunway, history: rawHistory, exported_at: ISO }`.
- Filename: `krab_costs_YYYY-MM-DD.json`

### Date Range Filter

Клиентская фильтрация `historyAll → historyFiltered` по ISO date. `currentHistPage` сбрасывается в 0 при изменении фильтра.

### Trend Toggle

Клик на кнопку в `.trend-toggle` → обновляет `trendMetric` → `renderTrendChart(rawHistory)`. Уничтожает и пересоздаёт Chart.js instance.

---

## Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| **Desktop** >= 1024px | `.grid-3` → 3 stat cards side-by-side; `.grid-2` → 2 columns (donut + finops); `.glass-nav` visible; tab bar hidden |
| **Tablet** 769–1023px | `.grid-3` → auto-fit `minmax(250px,1fr)` — 2 карточки в ряд если не влезает 3; `.grid-2` → auto-fit `minmax(300px,1fr)` может стать 1 column |
| **Mobile** <= 768px | `.glass-nav { display: none }` → `.glass-tab-bar { display: flex }` (72px снизу); `.runway-sub { grid-template-columns: 1fr 1fr }` — 2 колонки вместо 3; `.donut-wrapper { flex-direction: column }` — canvas над легендой; `.donut-canvas-wrap { width: 150px; height: 150px }`; `.grid-2x2 { grid-template-columns: 1fr }` — FinOps 1 column; `body padding-bottom: 80px` |

**Mobile Tab Bar (`.glass-tab-bar`):**
- `position: fixed; bottom: 0; height: 72px; padding-bottom: env(safe-area-inset-bottom)`
- Иконки: Hub / Chat / **Costs** (active: `.tab-item.active`) / Inbox / Swarm / Trans
- `.tab-item { color: rgba(255,255,255,0.5); font-size: 0.75rem; }`
- `.tab-item.active { color: #e0f2fe; }`

---

## Edge Cases

### Budget null state
- `monthly_limit_usd === null` → `#budget-not-set` visible, прогресс-бар ширина 0%, цвет `rgba(255,255,255,0.1)`, `#budget-limit` = "Not set", `#budget-remaining` = "—"
- Кнопка "Set Budget" всегда видна (не скрывается в null state)

### Runway infinite
- `runway_days > 3650` → текст "∞ (safe)", цвет `#34d399`
- `runway_days == 0` AND `monthly_cost == 0` → текст "∞", badge "БЕЗ РАСХОДОВ"

### Empty history
- `rawHistory = []` → trend chart рендерится из `MOCK_HISTORY_RAW` как fallback
- History table → empty state "Нет данных"
- Sparklines → не рендерятся (требуют >= 2 точек)

### Single-record history
- Trend indicators скрыты (недостаточно данных для сравнения)
- Sparklines не рендерятся

### Long model names
- `.bar-label` — `text-overflow: ellipsis; overflow: hidden; white-space: nowrap`
- Таблица — нет truncation, горизонтальный scroll через `.table-responsive { overflow-x: auto }`

### Fallbacks > 0
- `#fallbacks` получает `.text-warning` цвет

### API offline
- Все данные из MOCK
- `#api-status` → `.status-dot.warning` (yellow)
- Toast "Использую mock-данные" (нейтральный тип)

---

## Animation / Motion

| Element | Trigger | Animation | Duration | Easing |
|---------|---------|-----------|----------|--------|
| `.glass-card` hover | hover | `translateY(-4px)` + box-shadow | transition 0.3s | `cubic-bezier(0.25,0.8,0.25,1)` |
| `.stat-card` hover | hover | `translateY(-4px)` | transition 0.2s | ease |
| `.progress-fill` | data load | `width: 0% → N%` + color change | 0.5s | ease |
| `.bar-fill` | data load | `width: 0% → N%` | 0.5s | ease |
| `.skeleton` | loading | shimmer left-right | 2s infinite | linear |
| toast | appear | `fadeUp` (opacity 0→1, translateY 20px→0) | 0.3s | ease |
| `.modal-overlay` | open | `display: none → flex` (мгновенно) | — | — |
| stat card values | first load | count-up `animateValue()` | 1200ms | `1-(1-p)^4` (ease out quart) |
| body background blobs | — | static (fixed attachment) | — | — |
| `.status-dot` | always | `pulse` opacity+scale | 2s infinite | ease |

**`@keyframes glassShimmer`:** `background-position: -200% 0 → 200% 0`
**`@keyframes fadeUp`:** `opacity:0, translateY(20px) → opacity:1, translateY(0)`
**`@keyframes pulse`:** `opacity:1,scale:1 → opacity:0.5,scale:0.85 → opacity:1,scale:1`

---

## Accessibility Notes

### Contrast Ratios (dark theme)

| Pair | Ratio | WCAG |
|------|-------|------|
| `#bae6fd` (accent) on `#0a0a1a` (bg) | ~4.8:1 | AA pass |
| `#6ee7b7` (success) on `#0a0a1a` | ~4.5:1 | AA pass |
| `#fde68a` (warning) on `#0a0a1a` | ~5.2:1 | AA pass |
| `#fca5a5` (error) on `#0a0a1a` | ~3.9:1 | AA pass (large text only для small) |
| `rgba(255,255,255,0.72)` (.text-muted) on bg | ~3.5:1 | AA для large text |

### Touch Targets (min 44x44px)

| Element | Min size |
|---------|---------|
| "Set Budget" button | `padding: 0.6rem 1.25rem` ≈ 40px height — добавить `min-height: 44px` |
| "Export CSV" / "Export JSON" | `padding: 0.35rem 0.9rem` — маленькие, нужен `min-height: 44px` на mobile |
| Prev / Next pagination | аналогично — добавить `min-height: 44px` |
| Bell icon | `padding: 0.5rem` ≈ 36px — добавить `min-width: 44px; min-height: 44px` |
| `.legend-item` (donut) | padding 0.3rem — нужен `min-height: 36px` |
| `.banner-close` × | 1.1rem font — обернуть в `min-width: 32px; min-height: 32px` (acceptably small для dismiss) |

### ARIA

| Element | Required attribute |
|---------|--------------------|
| `<canvas id="donut-chart">` | `aria-label="Provider cost breakdown donut chart"` + `role="img"` |
| `<canvas id="trend-chart">` | `aria-label="Cost trend line chart"` + `role="img"` |
| `<canvas id="sparkline-*">` | `aria-hidden="true"` (декоративные) |
| `#hist-prev` / `#hist-next` | `aria-label="Previous page" / "Next page"` + `aria-disabled` при disabled |
| `#btn-export-csv` | `aria-label="Export history as CSV"` |
| `#btn-export-json` | `aria-label="Export full report as JSON"` |
| `#budget-modal` | `role="dialog" aria-modal="true" aria-labelledby="modal-title"` |
| `.runway-alert-banner` | `role="alert"` |
| `#api-status` | `aria-label="API status: [online|offline]"` — обновлять через JS |

### Keyboard Navigation

- Modal открывается / закрывается по Escape
- `#budget-input` должен получать focus при открытии modal (`input.focus()`)
- Tab order внутри modal: input → Cancel → Save → loop
- Pagination кнопки: `disabled` атрибут блокирует Tab

---

## Integration Path into FastAPI

### Текущий URL прототипа

Файл `src/web/prototypes/costs_v4_claude_design.html` уже доступен по:
```
http://127.0.0.1:8080/prototypes/costs_v4_claude_design
```
Маршрут `GET /prototypes/{page}` определён в `src/modules/web_app.py` (строки 7857–7868).

### Промоция в production `/v4/costs`

Текущий маршрут `GET /v4/costs` (строки 7897–7903) отдаёт `src/web/v4/costs.html`.

**Вариант A — замена файла (рекомендуется):**

Скопировать прототип в `src/web/v4/costs.html`:
```bash
cp src/web/prototypes/costs_v4_claude_design.html src/web/v4/costs.html
```
Изменить ссылку на CSS с относительного пути `/v4/liquid-glass.css` — убедиться что `<link rel="stylesheet" href="/v4/liquid-glass.css">` правильный (уже так в прототипе).

Маршрут в `web_app.py` изменений не требует.

**Вариант B — новый маршрут `GET /v4/costs-v4`:**

Добавить рядом с `v4_costs()` (после строки 7903):
```python
@self.app.get("/v4/costs-v4", response_class=HTMLResponse)
async def v4_costs_v4():
    """V4 Costs dashboard — новый дизайн."""
    page = config.BASE_DIR / "src" / "web" / "prototypes" / "costs_v4_claude_design.html"
    if page.exists():
        return FileResponse(page, headers=_no_store_headers())
    return HTMLResponse("<h1>Costs V4 not ready</h1>", status_code=404)
```
Позволяет сравнивать старый и новый дизайн одновременно.

**Вариант C — замена legacy HTML-константы:**

`src/modules/web_app_costs_dashboard.py` содержит константу `COSTS_DASHBOARD_HTML`. После промоции можно удалить этот файл и убрать его импорт из `web_app.py`.

### Test Plan

**Curl smoke tests:**
```bash
# Проверить что страница отдаётся
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/v4/costs
# Ожидается: 200

# Проверить что API endpoints работают
curl -s http://127.0.0.1:8080/api/costs/report | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/costs/budget | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/ops/runway | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/costs/history | python3 -m json.tool
```

**Manual browser checklist:**
1. Открыть `http://127.0.0.1:8080/v4/costs` — страница рендерится за < 2s
2. Budget null state: POST `{ "monthly_limit_usd": null }` через curl → убедиться что UI показывает "Бюджет не установлен"
3. Set Budget: нажать "Set Budget" → ввести 50 → Save → прогресс-бар зелёный
4. Export CSV: нажать "Export CSV" → файл скачивается, открывается в Numbers
5. Provider filter: кликнуть на легенду → история фильтруется → кликнуть ещё раз → фильтр снят
6. Trend toggle: переключить Cost/Calls/Tokens → граф перерисовывается
7. Mobile 375px: nav заменяется tab bar, stat cards в 1 column, donut над легендой
8. API offline: остановить Krab → статус-дот жёлтый, mock данные видны

**Добавить в тест-сьют** (`tests/unit/test_web_app.py` если существует, иначе новый файл):
```python
async def test_v4_costs_returns_html(client):
    resp = await client.get("/v4/costs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Costs Dashboard" in resp.text
```

---

*Источники: `src/web/prototypes/costs_v4_claude_design.html` (1131 строк), `src/web/v4/liquid-glass.css` (848 строк), `docs/CLAUDE_DESIGN_BRIEF_COSTS_V4.md` (298 строк).*
