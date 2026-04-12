# Krab Owner Panel — Dashboard Migration Spec

> Спецификация для миграции dashboard страниц с текущих версий на Gemini-прототипы.
> Реализация frontend: Gemini 3.1 Pro / GPT 5.4.
> Дата: 12.04.2026. Сервер: `http://127.0.0.1:8080`.

---

## Оглавление

1. [Обзор страниц и маппинг](#обзор-страниц-и-маппинг)
2. [Общая инфраструктура](#общая-инфраструктура)
3. [Страница /  (Landing / Main Dashboard)](#страница---landing--main-dashboard)
4. [Страница /costs](#страница-costs)
5. [Страница /inbox](#страница-inbox)
6. [Страница /swarm](#страница-swarm)
7. [Страница /translator](#страница-translator)
8. [Nano-прототипы (Ops Center + Voice Console)](#nano-прототипы-ops-center--voice-console)
9. [Порядок миграции](#порядок-миграции)
10. [API Gaps — нужные, но отсутствующие endpoints](#api-gaps)

---

## Обзор страниц и маппинг

| URL | Текущий файл | Прототип | Статус прототипа |
|-----|-------------|----------|-----------------|
| `/` | `src/web/index.html` (Krab Web Panel V2, ~4700 строк) | `prototypes/landing_v2.html` | Полный, с API calls |
| `/costs` | inline HTML в web_app.py (`/costs` route) | `prototypes/costs_v1.html` | Полный |
| `/inbox` | inline HTML в web_app.py (`/inbox` route) | `prototypes/inbox_v1.html` | Полный (mock данные, API закомментирован) |
| `/swarm` | inline HTML в web_app.py (`/swarm` route) | `prototypes/swarm_v1.html` | **НЕПОЛНЫЙ** — файл обрезан на 273 строке |
| `/translator` | inline HTML в web_app.py (`/translator` route) | `prototypes/translator_v1.html` | Полный |
| `/ops` _(новая)_ | нет | `prototypes/nano/ops_center.html` | Полный (mock данные) |
| `/voice` _(новая)_ | нет | `prototypes/nano/transcriber_console.html` | Полный (mock данные) |

Текущая `index.html` — монолитный файл (~381KB), содержащий весь Krab Web Panel V2:
главный dashboard + модели + assistant + ops. Это **НЕ** отдельные pages, а один SPA.

Прототип `prototypes/nano/index_redesign.html` — упрощённый вариант того же SPA
(nano-тема, те же API), является кандидатом для замены `index.html` целиком.

---

## Общая инфраструктура

### CSS тема
- Текущая: `/nano_theme.css` → `src/web/prototypes/nano/nano_theme.css`
  - переменные: `--bg-dark`, `--accent-cyan`, `--accent-purple`, `--state-ok/warn/error`
  - шрифт: Outfit (Google Fonts)
- Прототипы `*_v1.html` используют **inline CSS** со старой тёмной темой (`#1a1a2e / #16213e`)
  — при миграции стоит унифицировать под nano_theme

### Navbar (нужен на всех страницах)
```
[ / ] [ Stats/Ops ] [ Inbox ] [ Costs ] [ Swarm ] [ Translator ]
```
Активная страница — выделена. Sticky top.

### Auth
- Write-endpoints защищены header `X-Krab-Web-Key` или query `?token=`
- Read-endpoints открыты (в dev режиме)

### Polling
- Стандартный интервал: 10 сек (`setInterval(fetchData, 10000)`)
- Главная `/`: 15 сек (тяжёлые endpoints)

---

## Страница / — Landing / Main Dashboard

**Детальная спека:** `docs/DASHBOARD_LANDING_UPDATE_SPEC.md`

### Прототип
`prototypes/nano/index_redesign.html` — nano-redesign главной страницы.

### Текущая реализация
`src/web/index.html` — монолитный SPA, содержит:
- Core health карточки (`/api/health/lite`)
- Metrics row (Система / Black Box / RAG / Local Model)
- Services & Integrations (`/api/stats`)
- Model routing + catalog (`/api/model/catalog`, `/api/model/apply`)
- Ops Center v2 (queue, alerts, timeline)
- Assistant query box (`/api/assistant/query`)
- Full model management panel

### API endpoints (используются в index.html и прототипе)

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/stats` | GET | Основные метрики: bb_total, rag_total, degradation, local_model, checks |
| `/api/health` | GET | Полный health check всех сервисов |
| `/api/health/lite` | GET | Быстрый liveness ping (кешируется 5 сек) |
| `/api/links` | GET | Диагностические ссылки |
| `/api/model/recommend?profile=chat` | GET | Рекомендованная модель |
| `/api/model/catalog` | GET | Каталог провайдеров и моделей |
| `/api/model/catalog?force_refresh=true` | GET | Принудительное обновление каталога |
| `/api/model/apply` | POST | Применить выбранную модель/provider |
| `/api/model/feedback?profile=chat&top=1` | GET | Статистика feedback по модели |
| `/api/model/feedback` | POST | Отправить feedback |
| `/api/model/preflight` | POST | Preflight check перед запросом |
| `/api/model/provider-action` | POST | Действия над провайдером (probe, reset) |
| `/api/model/local/status` | GET | Статус LM Studio |
| `/api/ops/usage` | GET | Usage/cost статистика |
| `/api/ops/alerts` | GET | Активные алерты |
| `/api/ops/ack/{code}` | POST/DELETE | Подтвердить/отозвать алерт |
| `/api/timeline?limit=50` | GET | Последние события |
| `/api/queue` | GET | Состояние очереди |
| `/api/sla` | GET | SLA метрики |
| `/api/context/latest` | GET | Последний context checkpoint |
| `/api/context/checkpoint` | POST | Создать checkpoint |
| `/api/context/transition-pack` | POST | Transition pack |
| `/api/assistant/query` | POST | AI запрос через owner panel |
| `/api/assistant/attachment` | POST | Загрузить файл как контекст |
| `/api/openclaw/cloud` | GET | Cloud status |
| `/api/openclaw/channels/status` | GET | OpenClaw channels |
| `/api/openclaw/model-autoswitch/status` | GET | Autoswitch статус |
| `/api/openclaw/model-compat/probe` | GET | Probe совместимости модели |
| `/api/openclaw/routing/effective` | GET | Эффективный route |
| `/api/runtime/summary` | GET | **Единый summary endpoint** (предпочтительный для landing_v2) |

### Данные из `/api/runtime/summary`
```json
{
  "health": { "telegram": "ok", "gateway": "ok", "scheduler": "ok" },
  "route": { "model": "...", "provider": "...", "channel": "..." },
  "costs": { "total_cost": 0.12, "calls": 45, "by_model": {}, "by_channel": {} },
  "translator": { "profile": {}, "session": {} },
  "swarm": { "task_board": { "pending": 2, "done": 8 }, "listeners_enabled": true },
  "silence": { "enabled": false },
  "notify_enabled": true
}
```

### Layout прототипа (nano/index_redesign.html)
```
[Header: Krab Web Panel V2 | Token badge | Sync button]

[Core Liveness card] [Ecosystem Deep Health card]

[Система] [Black Box] [RAG Docs] [Local Model]   ← 4 metrics cards

[Сервисы & Интеграции] [Рекомендовано] [API Links]   ← left column
[Ops Center v2: Queue | Routing | Alerts | Timeline]  ← right column (2/3 width)

[OpenClaw Control Center: providers, model chain, presets]

[Assistant: prompt box + file attach + feedback]
```

---

## Страница /costs

**Детальная спека:** `docs/DASHBOARD_COSTS_UPDATE_SPEC.md`

### Прототип
`prototypes/costs_v1.html`

### Текущая реализация
`web_app.py`, route `/costs` → inline HTML (serving `costs_v1.html` или embedded HTML).

### API endpoints

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/costs/report` | GET | Полный costs report |

### Данные из `/api/costs/report`
```json
{
  "ok": true,
  "total_cost": 0.152,
  "total_calls": 45,
  "by_model": { "gemini-3-flash": { "cost": 0.08, "calls": 30 } },
  "total_tokens": 125000,
  "total_input_tokens": 95000,
  "total_output_tokens": 30000,
  "total_tool_calls": 15,
  "total_fallbacks": 2,
  "total_context_tokens": 45000,
  "avg_context_tokens": 3000,
  "by_channel": { "telegram": 12, "translator_mvp": 3 }
}
```

### Layout прототипа (costs_v1.html)
```
[Header: "Costs Dashboard"]
[Navbar]

[Total Cost] [Total Calls] [Total Tokens]   ← summary row

[FinOps Breakdown card]
  - Tool calls: 15 total
  - Fallbacks: 2 (warning badge если > 0)
  - Avg context: 3000 tokens/req
  - By channel: badges (telegram N, translator N)

[Cost Efficiency card]
  - Cost per request: $X.XX
  - Tokens per dollar: N

[By Model breakdown table]
  model | cost | calls | tokens

[Auto-refresh 10s]
```

---

## Страница /inbox

**Детальная спека:** `docs/DASHBOARD_INBOX_UPDATE_SPEC.md`

### Прототип
`prototypes/inbox_v1.html` — содержит mock данные, API calls закомментированы.

### Текущая реализация
`web_app.py`, route `/inbox` → inline HTML.

### API endpoints

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/inbox/status` | GET | Summary counts |
| `/api/inbox/items?limit=20&status=open` | GET | Список items |
| `/api/inbox/items?status=acked` | GET | Подтверждённые |
| `/api/inbox/items?status=all` | GET | Все items |
| `/api/inbox/update` | POST | Обновить item (ack/done/cancel) |
| `/api/inbox/stale-processing` | GET | Завязшие в processing |
| `/api/inbox/stale-open` | GET | Завязшие open items |
| `/api/inbox/stale-processing/remediate` | POST | Remediate stale-processing |
| `/api/inbox/stale-open/remediate` | POST | Remediate stale-open |
| `/api/inbox/create` | POST | Создать новый item |

### Данные

**`/api/inbox/status`:**
```json
{
  "ok": true,
  "total_items": 12,
  "open_items": 5,
  "attention_items": 2,
  "escalations": 0,
  "stale_processing": 0,
  "stale_open": 1
}
```

**`/api/inbox/items`:** item объект:
```json
{
  "id": "abc123",
  "severity": "warning",
  "title": "...",
  "kind": "Incident",
  "source": "APM",
  "created_at": "2026-04-12T09:45:00Z",
  "body": "подробный текст",
  "status": "open"
}
```

### Layout прототипа (inbox_v1.html)
```
[Header: "📬 Inbox"]
[Navbar]

[Status badges: Open(N) | Attention(N) | Escalations(N)]

[Summary Cards row]
  [Total Items] [Fresh Open] [Stale Open] [Pending Actions]

[Filter tabs: open | acked | all]

[Item List table]
  severity-emoji | title | kind | source | created_at | [Ack btn]
  ↓ expandable body text on click

[Quick Actions]
  [Bulk Ack Open] [Remediate Stale]

[Auto-refresh 10s]
```

**Severity emoji:** info=ℹ️, warning=⚠️, error=🔴, critical=🚨

---

## Страница /swarm

**Детальная спека:** `docs/DASHBOARD_SWARM_UPDATE_SPEC.md`

### Прототип
`prototypes/swarm_v1.html` — **ВНИМАНИЕ: файл обрезан (273 строки), только CSS, нет HTML body/JS**.
Необходимо создать заново по спеке.

### Текущая реализация
`web_app.py`, route `/swarm` → inline HTML.

### API endpoints

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/swarm/status` | GET | Память + команды |
| `/api/swarm/memory` | GET | Persistent memory по командам |
| `/api/swarm/teams` | GET | Список команд и их составы |
| `/api/swarm/task-board` | GET | Summary задач по статусам |
| `/api/swarm/tasks?team=&limit=20` | GET | Список задач с фильтром |
| `/api/swarm/artifacts?team=&limit=10` | GET | Последние артефакты раундов |
| `/api/swarm/artifacts/cleanup` | POST | Очистить артефакты |
| `/api/swarm/listeners` | GET | Статус listener accounts |
| `/api/swarm/listeners/toggle` | POST | Вкл/выкл listeners |
| `/api/swarm/stats` | GET | Статистика команд |
| `/api/swarm/reports` | GET | Последние отчёты |
| `/api/swarm/tasks/create` | POST | Создать задачу |
| `/api/swarm/task/{task_id}` | GET | Деталь задачи |
| `/api/swarm/task/{task_id}/update` | POST | Обновить задачу |
| `/api/swarm/task/{task_id}/priority` | POST | Изменить приоритет |
| `/api/swarm/task/{task_id}` | DELETE | Удалить задачу |
| `/api/swarm/team/{team_name}` | GET | Состояние конкретной команды |

### Данные

**`/api/swarm/task-board`:**
```json
{
  "ok": true,
  "summary": {
    "total": 10,
    "by_status": { "pending": 2, "in_progress": 1, "done": 6, "failed": 1 },
    "by_team": { "traders": 3, "coders": 4, "analysts": 2, "creative": 1 }
  }
}
```

**`/api/swarm/tasks`:** task объект:
```json
{
  "task_id": "t-001",
  "team": "coders",
  "title": "Implement cache layer",
  "status": "in_progress",
  "priority": "high",
  "created_at": "2026-04-11T20:00:00Z"
}
```

**`/api/swarm/artifacts`:** artifact объект:
```json
{
  "team": "analysts",
  "topic": "market research",
  "timestamp_iso": "2026-04-12T08:30:00Z",
  "duration_sec": 145,
  "result_preview": "Первые 200 символов результата..."
}
```

**`/api/swarm/listeners`:**
```json
{
  "listeners_enabled": true,
  "accounts": ["@p0lrdp_AI", "@p0lrdp_worldwide", "@hard2boof", "@opiodimeo"]
}
```

### Layout (нужно создать с нуля)
```
[Header: "🐝 Swarm Control"]
[Navbar]

[Секция: Task Board]
  [Status badges: pending(⏳ N) | in_progress(🔄 N) | done(✅ N) | failed(❌ N)]
  [By team breakdown: traders | coders | analysts | creative]
  [Task table: id | team | title | status | priority | created_at]
  [Filter by team dropdown] [Create Task button]

[Секция: Artifacts]
  [Last 10 round artifacts]
  [Table: team | topic | duration | timestamp]
  [Expandable result preview on click]

[Секция: Team Listeners]
  [Status badge: ON/OFF] [Toggle button]
  [Account list: @p0lrdp_AI, @p0lrdp_worldwide, @hard2boof, @opiodimeo]

[Секция: Swarm Memory]
  [По командам: traders | coders | analysts | creative]
  [Последние N записей в памяти команды]

[Auto-refresh 15s]
```

---

## Страница /translator

**Детальная спека:** `docs/DASHBOARD_TRANSLATOR_PAGE_SPEC.md`

### Прототип
`prototypes/translator_v1.html` — полный, с fetch к `/api/translator/status`.

### Текущая реализация
`web_app.py`, route `/translator` → inline HTML или `translator_v1.html`.

### API endpoints

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/translator/status` | GET | Статус переводчика |
| `/api/translator/readiness` | GET | Readiness snapshot |
| `/api/translator/control-plane` | GET | Control plane state |
| `/api/translator/session-inspector` | GET | Инспектор сессии |
| `/api/translator/mobile-readiness` | GET | Mobile device readiness |
| `/api/translator/delivery-matrix` | GET | Матрица доставки |
| `/api/translator/live-trial-preflight` | GET | Preflight для live trial |
| `/api/translator/history` | GET | История переводов |
| `/api/translator/languages` | GET | Доступные языки |
| `/api/translator/session/toggle` | POST | Вкл/выкл сессию |
| `/api/translator/session/start` | POST | Запустить сессию |
| `/api/translator/session/action` | POST | pause/resume/stop |
| `/api/translator/session/policy` | POST | Обновить политику |
| `/api/translator/session/runtime-tune` | POST | Runtime тюнинг |
| `/api/translator/session/quick-phrase` | POST | Быстрая фраза |
| `/api/translator/session/summary` | POST | Сводка сессии |
| `/api/translator/auto` | POST | Авто-режим toggle |
| `/api/translator/lang` | POST | Изменить язык |
| `/api/translator/translate` | POST | Ручной перевод |
| `/api/translator/bootstrap` | GET | Bootstrap данные |
| `/api/translator/mobile/onboarding` | GET | Mobile onboarding |
| `/api/translator/mobile/register` | POST | Регистрация устройства |
| `/api/translator/mobile/bind` | POST | Привязать устройство |
| `/api/translator/mobile/remove` | POST | Удалить устройство |
| `/api/translator/test` | GET | Тест переводчика |

### Данные из `/api/translator/status`
```json
{
  "ok": true,
  "profile": {
    "language_pair": "es-ru",
    "translation_mode": "bilingual",
    "voice_strategy": "voice-first",
    "ordinary_calls_enabled": true,
    "internet_calls_enabled": true
  },
  "session": {
    "session_status": "active",
    "translation_muted": false,
    "active_chats": [],
    "last_language_pair": "es-ru",
    "last_translated_original": "Buenos días...",
    "last_translated_translation": "Доброе утро...",
    "last_event": "translation_completed",
    "stats": {
      "total_translations": 15,
      "total_latency_ms": 45000
    }
  }
}
```

### Layout прототипа (translator_v1.html)
```
[Header: "🔄 Translator" | Status badge: Active/Idle/Paused]
[Navbar]

[Profile card]
  Language pair: es → ru
  Mode: bilingual
  Voice strategy: voice-first
  Ordinary calls: ON/OFF
  Internet calls: ON/OFF

[Session card]
  Status: active/idle | Muted: yes/no
  Active chats: список или "все"
  Stats: X translations | Avg latency Y ms

[Last Translation card]
  Original (boxed): "Buenos días..."
  Translation (boxed): "Доброе утро..."
  Direction badge: es→ru | Timestamp

[Auto-refresh 10s]
```

---

## Nano-прототипы (Ops Center + Voice Console)

Дополнительные страницы — **новые**, не заменяют существующие.
Маршруты нужно добавить в `web_app.py`.

### Ops Center (`/ops` или `/monitoring`)

**Прототип:** `prototypes/nano/ops_center.html` — полный mock, нет fetch.

**Нужные API:**

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/ops/alerts` | GET | Активные алерты |
| `/api/ops/ack/{code}` | POST/DELETE | Acknowledge / unack |
| `/api/timeline?limit=50` | GET | Live journal events |
| `/api/ops/metrics` | GET | Метрики |
| `/api/ops/diagnostics` | GET | Диагностика |

**Layout:**
```
[Header: "Ops/Monitoring Center" | System Status pill (ALL SYSTEMS NOMINAL)]

[Left panel: Active Alerts]
  alert-item(warn/critical) с [Acknowledge] кнопкой
  "No pending alerts" empty state

[Right panel: Live Journal Events table]
  TIME | LEVEL | SOURCE | MESSAGE
  цветовая маркировка строк: info/warn/error/critical

[Auto-refresh 5s]
```

**Примечание:** Для данных из journal нужен либо polling `/api/timeline`, либо WebSocket (gap — см. ниже).

### Voice Console (`/voice-console` или `/transcriber`)

**Прототип:** `prototypes/nano/transcriber_console.html` — полный UI mock, без fetch (только JS-demo).

**Нужные API:**

| Endpoint | Метод | Назначение |
|----------|-------|-----------|
| `/api/transcriber/status` | GET | Статус транскрайбера |
| `/api/voice/runtime` | GET | Voice runtime state |
| `/api/voice/toggle` | POST | Вкл/выкл voice |
| `/api/voice/profile` | GET | Voice profile |

**Layout:**
```
[Header: "Voice Console" | subtitle: Real-time Speech Recognition Interface]

[Left card: Controller]
  Status indicator (idle/listening/transcribing/error) + LED pulse
  Engine select: Local Whisper / Cloud API
  Language select: Auto / Russian / English
  [Start Recording] / [Stop Recording] buttons

[Right: Transcript Window]
  scrollable live transcript chunks
  timestamp [HH:MM] | partial/final text

[Без auto-refresh — push или polling /api/transcriber/status 2s]
```

---

## Порядок миграции

Рекомендуемый порядок по приоритету (effort vs impact):

### Фаза 1 — Быстрые победы (готовые прототипы, простой API)

1. **`/translator`** → `translator_v1.html`
   - Прототип полный, API уже есть (`/api/translator/status`)
   - Минимальный риск: страница изолированная
   - Только подключить реальный fetch (уже есть в прототипе, но закомментирован)

2. **`/costs`** → `costs_v1.html`
   - Прототип полный, API простой (`/api/costs/report`)
   - Добавить новые FinOps поля (total_tool_calls, total_fallbacks, by_channel)

3. **`/inbox`** → `inbox_v1.html`
   - Прототип полный, нужно раскомментировать fetch к `/api/inbox/status` и `/api/inbox/items`
   - Низкий риск, высокая польза (реальные данные вместо mock)

### Фаза 2 — Средние страницы

4. **`/swarm`** — создать заново по спеке выше
   - Прототип неполный, нужно создать с нуля
   - API все готовы (task-board, tasks, artifacts, listeners)
   - Ориентир по стилю: `costs_v1.html` / `inbox_v1.html`

### Фаза 3 — Новые страницы (nano)

5. **`/ops`** → `ops_center.html`
   - Добавить route в `web_app.py`
   - Подключить fetch к `/api/ops/alerts`, `/api/timeline`

6. **`/voice-console`** → `transcriber_console.html`
   - Добавить route в `web_app.py`
   - Решить вопрос с real-time: polling или WebSocket (см. API Gaps)

### Фаза 4 — Главная страница (высокий риск, высокий impact)

7. **`/`** → `nano/index_redesign.html`
   - Самый сложный: монолитный SPA с 30+ API calls
   - Сначала убедиться, что все остальные страницы мигрированы
   - Стратегия: заменить целиком, сохранив `index.html` как `index_v1_backup.html`
   - Основной endpoint для нового варианта: `/api/runtime/summary` (единый)

---

## API Gaps

Endpoints, которые нужны для прототипов, но отсутствуют или требуют доработки:

### 1. `/api/runtime/summary` — расширить данные
**Статус:** endpoint существует (`GET /api/runtime/summary`).
**Gap:** landing_v2 ожидает поля `swarm.task_board` и `translator.session` в одном response.
Нужно проверить, что эти поля уже включены.

### 2. WebSocket для live journal (Ops Center)
**Статус:** нет.
**Нужно:** либо polling `/api/timeline?limit=50` каждые 2-3 сек (достаточно для MVP),
либо `WS /ws/journal` для настоящего live stream.
**Рекомендация:** для MVP достаточно polling — добавлять WebSocket только если нужна sub-second latency.

### 3. Voice Console — transcription push/feed
**Статус:** `GET /api/transcriber/status` существует, но возвращает static state.
**Gap:** transcriber_console ожидает streaming transcript chunks.
**Рекомендация:** для MVP — polling `/api/transcriber/status` каждые 2 сек + накапливать chunks.

### 4. Swarm — `/api/swarm/task-board` ответная структура
**Статус:** endpoint зарегистрирован в web_app.py.
**Gap:** нужно проверить, что поля `by_status`, `by_team`, `total` присутствуют в response.

### 5. `/api/inbox/items` — поле `status` в фильтре
**Статус:** endpoint есть.
**Gap:** прототип использует `?status=open`, `?status=acked`, `?status=all` —
нужно проверить поддержку всех трёх значений.

### 6. Единый `/api/costs/report` с FinOps полями
**Статус:** endpoint есть, некоторые поля добавлены в session 5.
**Gap:** проверить наличие `total_tool_calls`, `total_fallbacks`, `total_context_tokens`,
`avg_context_tokens`, `by_channel` в реальном response.

---

## Ссылки на детальные спеки

- `docs/DASHBOARD_LANDING_UPDATE_SPEC.md` — главная страница
- `docs/DASHBOARD_COSTS_UPDATE_SPEC.md` — /costs
- `docs/DASHBOARD_INBOX_UPDATE_SPEC.md` — /inbox
- `docs/DASHBOARD_SWARM_UPDATE_SPEC.md` — /swarm
- `docs/DASHBOARD_TRANSLATOR_PAGE_SPEC.md` — /translator

---

_Сгенерировано: 12.04.2026. Автор: Claude (session 6 research agent)._
