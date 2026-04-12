# Krab Owner Panel — Dashboard Redesign Spec

> Спецификация для Gemini 3.1 Pro: полный редизайн Owner Panel (`http://127.0.0.1:8080`).
> Дата: 12.04.2026. Версия: 2.0 (post-session 7).
> Реализация frontend: Gemini 3.1 Pro (HTML/CSS/JS, без фреймворков).
> Никаких React/Vue/Angular — только vanilla JS + fetch API.

---

## Оглавление

1. [Контекст задачи](#1-контекст-задачи)
2. [Дизайн-система](#2-дизайн-система)
3. [Навигационная структура](#3-навигационная-структура)
4. [Полный реестр API endpoints](#4-полный-реестр-api-endpoints)
5. [Страницы — детальные wireframes](#5-страницы--детальные-wireframes)
   - 5.1 [/ — Main Dashboard](#51----main-dashboard)
   - 5.2 [/costs — Расходы](#52-costs--расходы)
   - 5.3 [/inbox — Входящие](#53-inbox--входящие)
   - 5.4 [/swarm — Multi-Agent Swarm](#54-swarm--multi-agent-swarm)
   - 5.5 [/translator — Переводчик](#55-translator--переводчик)
   - 5.6 [/ops — Ops/Monitoring Center](#56-ops--opsmonitoring-center)
   - 5.7 [/voice-console — Voice Console](#57-voice-console--voice-console)
   - 5.8 [/commands — Команды Telegram](#58-commands--команды-telegram)
   - 5.9 [/models — Модели и маршрутизация](#59-models--модели-и-маршрутизация)
   - 5.10 [/provisioning — Provisioning](#510-provisioning--provisioning)
6. [Общие компоненты](#6-общие-компоненты)
7. [Responsive + Mobile требования](#7-responsive--mobile-требования)
8. [Реал-тайм и polling](#8-реал-тайм-и-polling)
9. [Auth](#9-auth)
10. [Inline actions — кнопки с side-effects](#10-inline-actions--кнопки-с-side-effects)
11. [Порядок реализации](#11-порядок-реализации)

---

## 1. Контекст задачи

**Проблема:** Owner Panel стала перегружена. После session 7 в системе ~190+ API endpoints,
7 существующих HTML-страниц и ещё 3 новые (commands, models, provisioning).
Текущий `index.html` — монолит 381KB с 30+ fetch вызовами.
Текущий дизайн — разнородный (старые и новые страницы разным CSS).

**Цель:** единый, консистентный, тёмный, responsive интерфейс для всех страниц.
Каждая страница — отдельный HTML-файл с inline CSS/JS (server отдаёт их как статику).

**Технические ограничения:**
- Сервер: FastAPI на `http://127.0.0.1:8080`
- Статика отдаётся через `FileResponse` и `HTMLResponse`
- Нет WebSocket (кроме отдельного плана на ops журнал)
- Auth: header `X-Krab-Web-Key` или query `?token=` для write-endpoints
- Нет CORS ограничений (localhost-to-localhost)

---

## 2. Дизайн-система

### CSS переменные (базируется на nano_theme.css)

```css
:root {
  /* Фоны */
  --bg: #0a0a0f;
  --bg-card: #111118;
  --bg-hover: #1a1a24;
  --bg-sidebar: #0d0d14;

  /* Границы */
  --border: #1e1e2e;
  --border-active: #2e2e4e;

  /* Текст */
  --text: #e2e8f0;
  --text-muted: #64748b;
  --text-dim: #334155;

  /* Акценты */
  --accent: #7dd3fc;          /* cyan — основной */
  --accent-purple: #a78bfa;   /* purple — secondary */
  --accent-green: #34d399;    /* green — success/ok */

  /* Состояния */
  --state-ok: #22c55e;
  --state-warn: #f59e0b;
  --state-error: #ef4444;
  --state-critical: #dc2626;
  --state-info: #38bdf8;

  /* Типографика */
  --font-sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

  /* Размеры */
  --sidebar-width: 220px;
  --topbar-height: 48px;
  --radius: 6px;
  --radius-sm: 4px;
}
```

### Компоненты

**Карточки (`.card`):**
- background: `var(--bg-card)`
- border: `1px solid var(--border)`
- border-radius: `var(--radius)`
- padding: `16px`
- box-shadow: `0 1px 3px rgba(0,0,0,0.4)`

**Статус-пилюли (`.badge`):**
- `.badge-ok` → green
- `.badge-warn` → amber
- `.badge-error` → red
- `.badge-info` → cyan
- `.badge-muted` → gray
- Размер: `font-size: 11px`, `padding: 2px 8px`, `border-radius: 999px`

**Кнопки (`.btn`):**
- `.btn-primary` → accent cyan bg, dark text
- `.btn-danger` → red bg
- `.btn-ghost` → прозрачный, border accent
- `.btn-sm` → уменьшенный padding

**Индикатор активности (`.pulse-dot`):**
- 8px dot с CSS animation `pulse` (pulsing glow)
- Цвет по состоянию: зелёный/жёлтый/красный

**Таблицы (`.data-table`):**
- hover: `var(--bg-hover)`
- sticky header
- responsive: горизонтальный скролл на мобайле

**Toast уведомления:**
- position: fixed bottom-right
- fade in/out animation
- 3 секунды auto-dismiss

---

## 3. Навигационная структура

### Sidebar (desktop, ширина 220px)

```
┌──────────────────────┐
│  🦀 KRAB PANEL  v2   │  ← логотип + версия
│  ● online            │  ← pulse dot + статус
├──────────────────────┤
│ DASHBOARD            │
│  ○ / Main            │  ← иконка + название
│  ○ /commands         │
│  ○ /models           │
├──────────────────────┤
│ OPERATIONS           │
│  ○ /inbox            │
│  ○ /ops              │
│  ○ /voice-console    │
├──────────────────────┤
│ ANALYTICS            │
│  ○ /costs            │
│  ○ /swarm            │
│  ○ /translator       │
├──────────────────────┤
│ ADMIN                │
│  ○ /provisioning     │
├──────────────────────┤
│ [docs] [api]         │  ← footer links
└──────────────────────┘
```

Активный пункт: левый border `3px solid var(--accent)`, background `var(--bg-hover)`.

### Top bar (высота 48px)

```
[☰ Menu]  [Krab — /название-страницы]  [● Status: ok]  [🔄 Refresh]  [⚙ Token]
```

- **Левый край:** hamburger (мобайл) + breadcrumb
- **Центр:** статус-пилюля системы (из `/api/health/lite`)
- **Правый край:** кнопка рефреша + иконка настроек (токен)

### Mobile nav (< 768px)

- Sidebar скрыт, открывается через hamburger overlay
- Top bar сокращается: только логотип + hamburger
- Bottom tab bar с 5 ключевыми разделами (/, /inbox, /costs, /ops, /swarm)

---

## 4. Полный реестр API endpoints

Сгруппирован по функциональным доменам. Все endpoints — `http://127.0.0.1:8080`.

### 4.1 Системное здоровье

| Метод | Endpoint | Описание | Polling |
|-------|----------|----------|---------|
| GET | `/api/health` | Полный health check всех сервисов | 30s |
| GET | `/api/health/lite` | Быстрый liveness ping (кеш 5s) | 10s |
| GET | `/api/v1/health` | v1 health alias | — |
| GET | `/api/stats` | Основные метрики: bb_total, rag_total, degradation | 15s |
| GET | `/api/stats/caches` | Кеш-статистика | — |
| GET | `/api/uptime` | Uptime в секундах | 60s |
| GET | `/api/version` | Версия Краба, commit count, tests | — |
| GET | `/api/system/info` | CPU, RAM, disk (psutil) | 60s |
| GET | `/api/links` | Диагностические ссылки | — |
| GET | `/api/endpoints` | Список всех зарегистрированных endpoints | — |
| GET | `/api/runtime/summary` | Единый summary: health + route + costs + swarm | 15s |
| GET | `/api/runtime/operator-profile` | Профиль оператора | — |
| GET | `/api/runtime/handoff` | Handoff snapshot | — |
| POST | `/api/runtime/recover` | Попытка восстановления | action |
| POST | `/api/runtime/repair-active-shared-permissions` | Repair разрешений | action |
| POST | `/api/runtime/chat-session/clear` | Сброс chat session | action |

### 4.2 Модели и маршрутизация

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/model/status` | Текущий route + active model |
| GET | `/api/model/recommend?profile=chat` | Рекомендованная модель |
| GET | `/api/model/catalog` | Каталог провайдеров и моделей |
| GET | `/api/model/catalog?force_refresh=true` | Принудительное обновление |
| GET | `/api/model/explain` | Объяснение логики routing |
| GET | `/api/model/feedback?profile=chat&top=1` | Статистика feedback |
| GET | `/api/model/local/status` | Статус LM Studio |
| GET | `/api/model/local/status` | LM Studio состояние |
| POST | `/api/model/apply` | Применить выбранный model/provider |
| POST | `/api/model/switch` | Быстрый switch модели |
| POST | `/api/model/feedback` | Отправить feedback |
| POST | `/api/model/preflight` | Preflight check перед запросом |
| POST | `/api/model/provider-action` | Действия: probe, reset |
| POST | `/api/model/local/load-default` | Загрузить дефолтную LM Studio модель |
| POST | `/api/model/local/unload` | Выгрузить LM Studio модель |
| GET | `/api/thinking/status` | Текущий thinking_default |
| POST | `/api/thinking/set` | Установить thinking mode |
| GET | `/api/depth/status` | Алиас /thinking/status (depth терминология) |
| GET | `/api/openclaw/model-routing/status` | OpenClaw routing state |
| GET | `/api/openclaw/model-autoswitch/status` | Autoswitch статус |
| POST | `/api/openclaw/model-autoswitch/apply` | Применить autoswitch правила |
| GET | `/api/openclaw/model-compat/probe` | Probe совместимости модели |
| GET | `/api/openclaw/routing/effective` | Эффективный route |
| GET | `/api/openclaw/control-compat/status` | Control compatibility status |
| GET | `/api/ops/models` | Список моделей через ops |
| POST | `/api/ops/models` | Ops model actions |

### 4.3 Costs & Budget

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/costs/report` | Полный costs report |
| GET | `/api/costs/budget` | Просмотр бюджета |
| POST | `/api/costs/budget` | Установить бюджет |
| GET | `/api/costs/history` | История расходов по провайдерам |
| GET | `/api/ops/usage` | Usage/cost статистика |
| GET | `/api/ops/cost-report` | Ops cost report |
| GET | `/api/ops/runway` | Финансовый runway |
| GET | `/api/ops/executive-summary` | Executive summary |
| GET | `/api/ops/report` | Полный ops report |
| GET | `/api/ops/report/export` | Экспорт report |
| GET | `/api/ops/bundle` | Ops bundle |
| GET | `/api/ops/bundle/export` | Экспорт bundle |
| GET | `/api/ops/history` | История ops событий |

### 4.4 Alerts & Ops

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/ops/alerts` | Активные алерты |
| POST | `/api/ops/ack/{code}` | Acknowledge алерт |
| DELETE | `/api/ops/ack/{code}` | Unack алерт |
| GET | `/api/ops/diagnostics` | Диагностика |
| GET | `/api/ops/metrics` | Ops метрики |
| GET | `/api/timeline` | События журнала (limit=50) |
| GET | `/api/ops/timeline` | Алиас |
| GET | `/api/ops/runtime_snapshot` | Snapshot runtime state |
| POST | `/api/ops/maintenance/prune` | Очистка старых данных |
| GET | `/api/sla` | SLA метрики |
| GET | `/api/queue` | Состояние очереди |
| GET | `/api/system/diagnostics` | Системная диагностика |
| GET | `/api/ecosystem/health` | Полный ecosystem health |
| GET | `/api/ecosystem/health/export` | Экспорт ecosystem health |
| GET | `/api/ecosystem/capabilities` | Возможности экосистемы |

### 4.5 Inbox

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/inbox/status` | Summary counts |
| GET | `/api/inbox/items?limit=20&status=open` | Список items |
| GET | `/api/inbox/items?status=acked` | Подтверждённые |
| GET | `/api/inbox/items?status=all` | Все items |
| POST | `/api/inbox/update` | Обновить item (ack/done/cancel) |
| POST | `/api/inbox/create` | Создать новый item |
| GET | `/api/inbox/stale-processing` | Завязшие в processing |
| GET | `/api/inbox/stale-open` | Завязшие open items |
| POST | `/api/inbox/stale-processing/remediate` | Remediate stale-processing |
| POST | `/api/inbox/stale-open/remediate` | Remediate stale-open |

### 4.6 Swarm

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/swarm/status` | Память + команды |
| GET | `/api/swarm/memory` | Persistent memory по командам |
| GET | `/api/swarm/teams` | Список команд и составы |
| GET | `/api/swarm/task-board` | Summary задач по статусам |
| GET | `/api/swarm/tasks?team=&limit=20` | Список задач с фильтром |
| GET | `/api/swarm/task/{task_id}` | Деталь задачи |
| POST | `/api/swarm/tasks/create` | Создать задачу |
| POST | `/api/swarm/task/{task_id}/update` | Обновить задачу |
| POST | `/api/swarm/task/{task_id}/priority` | Изменить приоритет |
| DELETE | `/api/swarm/task/{task_id}` | Удалить задачу |
| GET | `/api/swarm/team/{team_name}` | Состояние конкретной команды |
| GET | `/api/swarm/artifacts?team=&limit=10` | Последние артефакты |
| POST | `/api/swarm/artifacts/cleanup` | Очистить артефакты |
| GET | `/api/swarm/listeners` | Статус listener accounts |
| POST | `/api/swarm/listeners/toggle` | Вкл/выкл listeners |
| GET | `/api/swarm/stats` | Статистика команд |
| GET | `/api/swarm/reports` | Последние отчёты |

### 4.7 Translator

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/translator/status` | Статус переводчика |
| GET | `/api/translator/readiness` | Readiness snapshot |
| GET | `/api/translator/control-plane` | Control plane state |
| GET | `/api/translator/session-inspector` | Инспектор сессии |
| GET | `/api/translator/mobile-readiness` | Mobile device readiness |
| GET | `/api/translator/delivery-matrix` | Матрица доставки |
| GET | `/api/translator/live-trial-preflight` | Preflight для live trial |
| GET | `/api/translator/history` | История переводов |
| GET | `/api/translator/languages` | Доступные языки |
| GET | `/api/translator/bootstrap` | Bootstrap данные |
| GET | `/api/translator/test` | Тест переводчика |
| GET | `/api/translator/mobile/onboarding` | Mobile onboarding пакет |
| POST | `/api/translator/session/toggle` | Вкл/выкл сессию |
| POST | `/api/translator/session/start` | Запустить сессию |
| POST | `/api/translator/session/action` | pause/resume/stop |
| POST | `/api/translator/session/policy` | Обновить политику |
| POST | `/api/translator/session/runtime-tune` | Runtime тюнинг |
| POST | `/api/translator/session/quick-phrase` | Быстрая фраза |
| POST | `/api/translator/session/summary` | Сводка сессии |
| POST | `/api/translator/session/escalate` | Escalation |
| POST | `/api/translator/auto` | Авто-режим toggle |
| POST | `/api/translator/lang` | Изменить язык |
| POST | `/api/translator/translate` | Ручной перевод |
| POST | `/api/translator/mobile/onboarding/export` | Экспорт onboarding |
| POST | `/api/translator/mobile/register` | Регистрация устройства |
| POST | `/api/translator/mobile/trial-prep` | Trial prep |
| POST | `/api/translator/mobile/bind` | Привязать устройство |
| POST | `/api/translator/mobile/remove` | Удалить устройство |

### 4.8 Voice

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/voice/runtime` | Voice runtime state |
| GET | `/api/voice/profile` | Voice profile |
| POST | `/api/voice/runtime/update` | Обновить voice runtime |
| POST | `/api/voice/toggle` | Вкл/выкл voice |
| GET | `/api/transcriber/status` | Статус транскрайбера |

### 4.9 Browser & Automation

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/browser/status` | Статус браузера (Chrome CDP) |
| GET | `/api/browser/tabs` | Открытые вкладки |
| POST | `/api/browser/navigate` | Навигация |
| POST | `/api/browser/screenshot` | Скриншот |
| POST | `/api/browser/read` | Чтение страницы |
| POST | `/api/browser/js` | Выполнение JS |
| GET | `/api/openclaw/browser-smoke` | Browser smoke test |
| GET | `/api/openclaw/browser-mcp-readiness` | Browser MCP readiness |
| GET | `/api/openclaw/photo-smoke` | Photo smoke test |
| POST | `/api/openclaw/browser/start` | Запустить браузер |
| POST | `/api/openclaw/browser/open-owner-chrome` | Открыть Chrome profile |

### 4.10 OpenClaw & Cloud

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/openclaw/cloud` | Cloud status |
| GET | `/api/openclaw/cloud/diagnostics` | Cloud diagnostics |
| GET | `/api/openclaw/cloud/runtime-check` | Cloud runtime check |
| POST | `/api/openclaw/cloud/switch-tier` | Переключить cloud tier |
| GET | `/api/openclaw/cloud/tier/state` | Текущий tier state |
| POST | `/api/openclaw/cloud/tier/reset` | Сброс tier |
| GET | `/api/openclaw/channels/status` | Статус каналов OpenClaw |
| POST | `/api/openclaw/channels/runtime-repair` | Repair каналов |
| POST | `/api/openclaw/channels/signal-guard-run` | Signal guard run |
| GET | `/api/openclaw/report` | OpenClaw report |
| GET | `/api/openclaw/deep-check` | Deep check |
| GET | `/api/openclaw/remediation-plan` | Plan восстановления |
| GET | `/api/openclaw/runtime-config` | Runtime конфиг |
| POST | `/api/diagnostics/smoke` | Smoke test |

### 4.11 Cron Jobs

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/openclaw/cron/status` | Статус cron scheduler |
| GET | `/api/openclaw/cron/jobs` | Список cron jobs |
| POST | `/api/openclaw/cron/jobs/create` | Создать job |
| POST | `/api/openclaw/cron/jobs/toggle` | Вкл/выкл job |
| POST | `/api/openclaw/cron/jobs/remove` | Удалить job |

### 4.12 ACL, Notify, Silence

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/userbot/acl/status` | ACL runtime state |
| POST | `/api/userbot/acl/update` | Обновить ACL |
| GET | `/api/notify/status` | Статус tool narrations |
| POST | `/api/notify/toggle` | Вкл/выкл notify |
| POST | `/api/notify` | Отправить уведомление |
| GET | `/api/silence/status` | Режим тишины |
| POST | `/api/silence/toggle` | Вкл/выкл тишину |

### 4.13 Commands & Assistant

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/commands` | Список Telegram команд |
| GET | `/api/assistant/capabilities` | Возможности assistant |
| POST | `/api/assistant/query` | AI запрос через owner panel |
| POST | `/api/assistant/attachment` | Загрузить файл как контекст |

### 4.14 Provisioning

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/provisioning/templates?entity=agent` | Шаблоны provisioning |
| GET | `/api/provisioning/drafts?status=&limit=20` | Список drafts |
| POST | `/api/provisioning/drafts` | Создать draft |
| GET | `/api/provisioning/preview/{draft_id}` | Preview diff |
| POST | `/api/provisioning/apply/{draft_id}?confirm=true` | Применить draft |

### 4.15 Разное

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/policy` | Текущий runtime policy |
| GET | `/api/policy/matrix` | Policy matrix |
| GET | `/api/capabilities/registry` | Capability registry |
| GET | `/api/channels/capabilities` | Channel capabilities |
| GET | `/api/ctx` | Context state |
| GET | `/api/reactions/stats` | Статистика реакций |
| GET | `/api/mood/{chat_id}` | Mood конкретного чата |
| POST | `/api/context/checkpoint` | Создать checkpoint |
| POST | `/api/context/transition-pack` | Transition pack |
| GET | `/api/context/latest` | Последний checkpoint |
| POST | `/api/krab/restart_userbot` | Перезапустить userbot |

---

## 5. Страницы — детальные wireframes

### 5.1 / — Main Dashboard

**Файл:** `src/web/index.html`
**Polling:** 15s основной, 10s health

**Primary API:** `/api/runtime/summary` (единый endpoint)
**Secondary API:** `/api/health/lite`, `/api/ops/alerts`, `/api/queue`

#### Wireframe (desktop, 2-column layout)

```
┌─ TOPBAR ──────────────────────────────────────────────────────────────┐
│  🦀 Main Dashboard              [● Telegram: ok] [● Gateway: ok]  [↻] │
└───────────────────────────────────────────────────────────────────────┘
┌─ SIDEBAR ─┐  ┌─ MAIN CONTENT ────────────────────────────────────────┐
│  [nav]    │  │                                                        │
│           │  │  ┌──── LIVENESS ─────┐  ┌──── ROUTE ────────────────┐ │
│           │  │  │ ● Telegram   ok   │  │ Model: gemini-3-pro        │ │
│           │  │  │ ● Gateway    ok   │  │ Provider: google           │ │
│           │  │  │ ● Scheduler  ok   │  │ Channel: telegram          │ │
│           │  │  │ ● Voice      ok   │  └───────────────────────────┘ │
│           │  │  └──────────────────┘                                  │
│           │  │                                                        │
│           │  │  ┌── TODAY ──────────────────────────────────────────┐ │
│           │  │  │ Cost: $0.12  │ Calls: 45  │ Tokens: 125k  │ ↑Avg │ │
│           │  │  └───────────────────────────────────────────────────┘ │
│           │  │                                                        │
│           │  │  ┌── ALERTS (N) ──────────┐  ┌── QUEUE ────────────┐  │
│           │  │  │ ⚠ High latency  [Ack]  │  │ Pending: 2         │  │
│           │  │  │ ℹ Model switched [Ack] │  │ Processing: 1      │  │
│           │  │  └───────────────────────┘  └────────────────────┘  │
│           │  │                                                        │
│           │  │  ┌── SWARM STATUS ────────┐  ┌── TRANSLATOR ───────┐  │
│           │  │  │ Tasks: 10 total        │  │ Pair: es→ru         │  │
│           │  │  │ Done: 8 / Pending: 2   │  │ Session: active     │  │
│           │  │  │ Listeners: ON          │  │ 15 translations     │  │
│           │  │  └───────────────────────┘  └────────────────────┘  │
│           │  │                                                        │
│           │  │  ┌── QUICK ACTIONS ──────────────────────────────────┐ │
│           │  │  │ [↻ Health Recheck] [Clear Session] [Toggle Notify]│ │
│           │  │  └───────────────────────────────────────────────────┘ │
└───────────┘  └────────────────────────────────────────────────────────┘
```

**Данные из `/api/runtime/summary`:**
```json
{
  "health": { "telegram": "ok", "gateway": "ok", "scheduler": "ok" },
  "route": { "model": "...", "provider": "...", "channel": "..." },
  "costs": { "total_cost": 0.12, "calls": 45, "by_model": {} },
  "translator": { "profile": {}, "session": {} },
  "swarm": { "task_board": { "pending": 2, "done": 8 }, "listeners_enabled": true },
  "silence": { "enabled": false },
  "notify_enabled": true
}
```

**Inline actions:**
- `[↻ Health Recheck]` → POST `/api/runtime/recover`
- `[Clear Session]` → POST `/api/runtime/chat-session/clear` (с confirm dialog)
- `[Toggle Notify]` → POST `/api/notify/toggle`
- `[Silence ON/OFF]` → POST `/api/silence/toggle`
- `[Ack]` на каждом алерте → POST `/api/ops/ack/{code}`

---

### 5.2 /costs — Расходы

**Файл:** `src/web/costs.html`
**Polling:** 10s

**Primary API:** `/api/costs/report`
**Secondary API:** `/api/costs/budget`, `/api/costs/history`

#### Wireframe

```
┌─ TOPBAR ──────────────────────────────────────────────────────────────┐
│  💰 Costs & Budget                                            [↻ 10s] │
└───────────────────────────────────────────────────────────────────────┘

┌── SUMMARY ROW ──────────────────────────────────────────────────────────┐
│  [Total Cost $0.152]  [Total Calls 45]  [Total Tokens 125K]  [Budget]  │
└─────────────────────────────────────────────────────────────────────────┘

┌── BUDGET CARD ─────────────────────────┐  ┌── EFFICIENCY ────────────────┐
│ Budget: $5.00/day                      │  │ Cost/request: $0.0034        │
│ Spent today: $0.152 (3%)               │  │ Tokens/$: 822k               │
│ Progress bar: ██░░░░░░░░░░░░ 3%        │  │ Avg context: 3000 tok/req    │
│ [Set Budget]                           │  │ Fallbacks: 2 ⚠               │
└────────────────────────────────────────┘  └─────────────────────────────┘

┌── BY MODEL (таблица) ──────────────────────────────────────────────────┐
│ Model               │ Cost    │ Calls │ Tokens  │ Avg cost            │
│ gemini-3-flash      │ $0.080  │ 30    │ 80K     │ $0.0027             │
│ gemini-3-pro        │ $0.072  │ 15    │ 45K     │ $0.0048             │
└─────────────────────────────────────────────────────────────────────────┘

┌── BY CHANNEL ────────────────────────────────────────────────────────┐
│ telegram: 12 calls  │  translator_mvp: 3 calls  │  swarm: 30 calls   │
└──────────────────────────────────────────────────────────────────────┘

┌── HISTORY (last 7 days) ────────────────────────────────────────────┐
│ Date       │ Provider     │ Cost    │ Calls                          │
│ 2026-04-12 │ google       │ $0.152  │ 45                             │
│ 2026-04-11 │ google       │ $0.098  │ 31                             │
└─────────────────────────────────────────────────────────────────────┘
```

**Inline actions:**
- `[Set Budget]` → POST `/api/costs/budget` body: `{"daily_limit": X}`
- Кнопка `[Export]` → открыть `/api/ops/report/export` в новой вкладке

---

### 5.3 /inbox — Входящие

**Файл:** `src/web/inbox.html`
**Polling:** 10s

**Primary API:** `/api/inbox/status`, `/api/inbox/items`

#### Wireframe

```
┌─ TOPBAR ──────────────────────────────────────────────────────────────┐
│  📬 Inbox                     [Open: 5]  [Attention: 2]  [Esc: 0]  [↻] │
└───────────────────────────────────────────────────────────────────────┘

┌── STATUS BADGES ─────────────────────────────────────────────────────┐
│  [Open: 5]  [Attention: 2]  [Escalations: 0]  [Stale: 1]            │
└──────────────────────────────────────────────────────────────────────┘

┌── FILTER TABS ───────────────────────────────────────────────────────┐
│  [open ●]   [acked]   [all]                                          │
└──────────────────────────────────────────────────────────────────────┘

┌── ITEM LIST ─────────────────────────────────────────────────────────┐
│ Sev │ Title              │ Kind     │ Source │ Time    │ Actions      │
│ ⚠   │ High latency spike │ Incident │ APM    │ 09:45   │ [Ack] [View]│
│ ℹ   │ Model switched     │ Info     │ Router │ 08:12   │ [Ack]       │
│ 🔴  │ Gateway timeout    │ Error    │ GW     │ 07:55   │ [Ack] [View]│
│     ▼ (click to expand body)                                         │
└──────────────────────────────────────────────────────────────────────┘

┌── QUICK ACTIONS ──────────────────────────────────────────────────────┐
│ [Bulk Ack All Open]  [Remediate Stale Processing]  [+ Create Item]   │
└───────────────────────────────────────────────────────────────────────┘
```

**Severity иконки:** info=ℹ, warning=⚠, error=🔴, critical=🚨

**Inline actions:**
- `[Ack]` → POST `/api/inbox/update` body: `{"id": "...", "action": "ack"}`
- `[Bulk Ack All Open]` → POST `/api/inbox/update` для каждого open item
- `[Remediate Stale Processing]` → POST `/api/inbox/stale-processing/remediate`
- `[Remediate Stale Open]` → POST `/api/inbox/stale-open/remediate`
- `[+ Create Item]` → modal с формой → POST `/api/inbox/create`

---

### 5.4 /swarm — Multi-Agent Swarm

**Файл:** `src/web/swarm.html`
**Polling:** 15s

**Primary API:** `/api/swarm/task-board`, `/api/swarm/tasks`, `/api/swarm/artifacts`
**Secondary API:** `/api/swarm/listeners`, `/api/swarm/memory`, `/api/swarm/stats`

#### Wireframe

```
┌─ TOPBAR ──────────────────────────────────────────────────────────────┐
│  🐝 Swarm Control    [Listeners: ON]  [Tasks: 10]  [Done: 8]      [↻] │
└───────────────────────────────────────────────────────────────────────┘

┌── TASK BOARD ─────────────────────────────────────────────────────────┐
│  ⏳ Pending: 2   🔄 In Progress: 1   ✅ Done: 8   ❌ Failed: 1        │
│                                                                        │
│  By team:  traders: 3  │  coders: 4  │  analysts: 2  │  creative: 1   │
└────────────────────────────────────────────────────────────────────────┘

┌── TASKS ──────────────────────────────────────────────────────────────┐
│ Filter: [All teams ▼]  [All statuses ▼]              [+ Create Task]  │
│                                                                        │
│ ID    │ Team     │ Title                 │ Status      │ Priority      │
│ t-001 │ coders   │ Implement cache layer │ in_progress │ ⬆ high       │
│ t-002 │ analysts │ Market research BTC   │ pending     │ = medium      │
│        [View] [Edit Priority] [Delete]                                 │
└────────────────────────────────────────────────────────────────────────┘

┌── ARTIFACTS (last 10) ────────────────────────────────────────────────┐
│ Team     │ Topic              │ Duration │ Time    │                   │
│ analysts │ market research    │ 145s     │ 08:30   │ [Preview ▼]       │
│ coders   │ cache layer design │ 87s      │ 06:15   │ [Preview ▼]       │
│           ▼ expandable: первые 500 символов результата                │
│                                    [Cleanup Old]                       │
└────────────────────────────────────────────────────────────────────────┘

┌── LISTENERS ──────────────────────────────────────────────────────────┐
│ Status: ● ON                      [Toggle Listeners]                   │
│ Accounts: @p0lrdp_AI  @p0lrdp_worldwide  @hard2boof  @opiodimeo       │
└────────────────────────────────────────────────────────────────────────┘

┌── MEMORY (tabs per team) ─────────────────────────────────────────────┐
│ [traders] [coders] [analysts] [creative]                               │
│ ┌─ coders memory ──────────────────────────────────────────────────┐  │
│ │ 2026-04-12 08:30: Завершили cache layer реализацию               │  │
│ │ 2026-04-11 20:00: Начали анализ bottlenecks                      │  │
│ └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

**Inline actions:**
- `[+ Create Task]` → modal с полями team/title/priority → POST `/api/swarm/tasks/create`
- `[Edit Priority]` → inline dropdown → POST `/api/swarm/task/{id}/priority`
- `[Delete]` → confirm → DELETE `/api/swarm/task/{id}`
- `[Toggle Listeners]` → POST `/api/swarm/listeners/toggle`
- `[Cleanup Old]` → POST `/api/swarm/artifacts/cleanup`

---

### 5.5 /translator — Переводчик

**Файл:** `src/web/translator.html`
**Polling:** 10s

**Primary API:** `/api/translator/status`
**Secondary API:** `/api/translator/readiness`, `/api/translator/history`, `/api/translator/languages`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  🔄 Translator           [● Active]  [es → ru]  [bilingual]      [↻] │
└──────────────────────────────────────────────────────────────────────┘

┌── PROFILE CARD ──────────────────┐  ┌── SESSION CARD ──────────────────┐
│ Language pair: es → ru           │  │ Status: ● active                 │
│ Mode: bilingual                  │  │ Muted: no                        │
│ Voice strategy: voice-first      │  │ Active chats: all                │
│ Ordinary calls: ● ON             │  │ Translations: 15                 │
│ Internet calls: ● ON             │  │ Avg latency: 3000ms              │
│ [Change Language]                │  │ [Pause] [Stop]                   │
└──────────────────────────────────┘  └──────────────────────────────────┘

┌── LAST TRANSLATION ──────────────────────────────────────────────────┐
│ Original (es):   "Buenos días, ¿cómo estás?"                         │
│ Translation (ru): "Доброе утро, как дела?"                           │
│ Direction: es → ru  │  Time: 09:45:12                                │
└──────────────────────────────────────────────────────────────────────┘

┌── QUICK PHRASE ──────────────────────────────────────────────────────┐
│ [Phrase text input ........................................] [Send]    │
└──────────────────────────────────────────────────────────────────────┘

┌── HISTORY (last 10) ─────────────────────────────────────────────────┐
│ Time  │ Original                    │ Translation         │ Lang     │
│ 09:45 │ Buenos días...              │ Доброе утро...      │ es→ru    │
└──────────────────────────────────────────────────────────────────────┘

┌── READINESS ─────────────────────────────────────────────────────────┐
│ [Run Preflight Check]  →  показывает live trial preflight результат  │
└──────────────────────────────────────────────────────────────────────┘
```

**Inline actions:**
- `[Change Language]` → modal с select пары → POST `/api/translator/lang`
- `[Pause]` / `[Resume]` → POST `/api/translator/session/action` body: `{"action": "pause"}`
- `[Stop]` → confirm → POST `/api/translator/session/action` body: `{"action": "stop"}`
- `[Send]` quick phrase → POST `/api/translator/session/quick-phrase`
- `[Run Preflight Check]` → GET `/api/translator/live-trial-preflight` → показать результат

---

### 5.6 /ops — Ops/Monitoring Center

**Файл:** `src/web/ops.html`
**Polling:** 5s alerts, 10s timeline, 30s metrics

**Primary API:** `/api/ops/alerts`, `/api/timeline`, `/api/ops/metrics`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  🔍 Ops Center       [● ALL SYSTEMS NOMINAL]  [Alerts: 0]       [↻] │
└──────────────────────────────────────────────────────────────────────┘

┌── METRICS ROW ───────────────────────────────────────────────────────┐
│ [Queue: 2 pending] [SLA: 99.2%] [Uptime: 4h 23m] [Requests: 45/h]  │
└──────────────────────────────────────────────────────────────────────┘

┌── LEFT: ACTIVE ALERTS ──────────┐  ┌── RIGHT: LIVE JOURNAL ──────────┐
│ ⚠ High latency on translate     │  │ TIME   LVL  SRC      MESSAGE    │
│   code: LAT_001  [Ack]          │  │ 09:47  INFO gateway  Request ok │
│ ─────────────────────────────── │  │ 09:45  WARN router   Fallback   │
│ No more alerts                  │  │ 09:44  INFO swarm    Round done │
│                                 │  │ 09:43  ERR  telegram Timeout    │
│ [Run Smoke Test]                │  │ [Load more]                     │
└─────────────────────────────────┘  └─────────────────────────────────┘

┌── DIAGNOSTICS ────────────────────────────────────────────────────────┐
│ [System Diagnostics] [Ecosystem Health] [OpenClaw Deep Check]         │
│ → Вывод результата inline в expandable секции                         │
└────────────────────────────────────────────────────────────────────────┘

┌── MAINTENANCE ────────────────────────────────────────────────────────┐
│ [Prune Old Data]  [Runtime Recover]  [Repair Permissions]             │
└────────────────────────────────────────────────────────────────────────┘
```

**Строки журнала:**
- INFO → цвет `--state-info` (cyan dim)
- WARN → цвет `--state-warn` (amber)
- ERROR/ERR → цвет `--state-error` (red)
- CRITICAL → цвет `--state-critical` (bright red), bold

**Inline actions:**
- `[Ack]` → POST `/api/ops/ack/{code}`
- `[Run Smoke Test]` → POST `/api/diagnostics/smoke` → показать результат
- `[System Diagnostics]` → GET `/api/system/diagnostics` → expandable
- `[Ecosystem Health]` → GET `/api/ecosystem/health` → expandable
- `[OpenClaw Deep Check]` → GET `/api/openclaw/deep-check` → expandable
- `[Prune Old Data]` → POST `/api/ops/maintenance/prune` (с confirm)
- `[Runtime Recover]` → POST `/api/runtime/recover`
- `[Repair Permissions]` → POST `/api/runtime/repair-active-shared-permissions`

---

### 5.7 /voice-console — Voice Console

**Файл:** `src/web/voice-console.html`
**Polling:** 2s (status), нет auto-refresh на transcript

**Primary API:** `/api/transcriber/status`, `/api/voice/runtime`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  🎙 Voice Console         [Status: idle]  [Engine: Local Whisper] [↻] │
└──────────────────────────────────────────────────────────────────────┘

┌── CONTROLLER ──────────────────────┐  ┌── TRANSCRIPT WINDOW ──────────┐
│                                    │  │                                │
│   ◉ idle                           │  │  [09:47] Transcribing...       │
│   (pulsing dot: green=ok,          │  │                                │
│    yellow=listening,               │  │  [09:46] "Buenos días, ¿cómo   │
│    red=error)                      │  │           estás?"              │
│                                    │  │                                │
│  Engine:  [Local Whisper ▼]        │  │  [09:44] "Хорошо, спасибо"    │
│  Language:[Auto ▼]                 │  │                                │
│                                    │  │  [09:43] "¿Puedes repetir?"   │
│  [▶ Start Recording]               │  │                                │
│  [■ Stop Recording]                │  │                   [Clear]      │
│                                    │  └────────────────────────────────┘
│  Voice Reply: ● ON                 │
│  [Toggle Voice]                    │
└────────────────────────────────────┘

┌── VOICE PROFILE ─────────────────────────────────────────────────────┐
│ Profile data из /api/voice/profile (rate, style, language, etc.)     │
└──────────────────────────────────────────────────────────────────────┘
```

**Polling стратегия:** каждые 2s GET `/api/transcriber/status` → если появился новый
`last_chunk` → добавить в transcript window с timestamp.

**Inline actions:**
- `[Toggle Voice]` → POST `/api/voice/toggle`
- `[Start/Stop Recording]` → POST `/api/voice/runtime/update`

---

### 5.8 /commands — Команды Telegram

**Файл:** `src/web/commands.html`
**Polling:** нет (статичные данные), refresh by user request

**Primary API:** `/api/commands`, `/api/userbot/acl/status`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  ⌨ Telegram Commands & ACL                            [↻ Refresh]    │
└──────────────────────────────────────────────────────────────────────┘

┌── COMMANDS LIST ──────────────────────────────────────────────────────┐
│ Command        │ Description                  │ Access Level           │
│ !status        │ статус системы               │ owner                  │
│ !model         │ маршрутизация модели         │ owner                  │
│ !clear         │ очистить историю             │ owner                  │
│ !voice         │ голосовой профиль            │ owner                  │
│ !notify        │ toggle tool narrations       │ owner                  │
│ !тишина        │ режим тишины                 │ owner                  │
│ !translator    │ переводчик                   │ owner                  │
│ !swarm         │ multi-agent teams            │ owner                  │
│ !search        │ веб-поиск                    │ partial                │
│ !inbox         │ owner inbox                  │ owner                  │
│ !watch         │ proactive watch              │ owner                  │
│ !remember      │ запомнить                    │ owner                  │
│ !recall        │ вспомнить                    │ owner                  │
│ !help          │ справка                      │ all                    │
└───────────────────────────────────────────────────────────────────────┘

┌── ACL STATUS ─────────────────────────────────────────────────────────┐
│ Owner: @username                                                       │
│ Owner subjects: [chat_id_list]                                         │
│ Partial access commands: [!search, ...]                                │
│                                                                         │
│ [Update ACL]  → форма для изменения subjects                           │
└────────────────────────────────────────────────────────────────────────┘

┌── NOTIFY & SILENCE ───────────────────────────────────────────────────┐
│ Tool Narrations: ● ON    [Toggle]                                      │
│ Silence Mode:   ○ OFF   [Toggle]                                       │
└────────────────────────────────────────────────────────────────────────┘
```

**Inline actions:**
- `[Update ACL]` → modal с формой → POST `/api/userbot/acl/update`
- `[Toggle Narrations]` → POST `/api/notify/toggle`
- `[Toggle Silence]` → POST `/api/silence/toggle`

---

### 5.9 /models — Модели и маршрутизация

**Файл:** `src/web/models.html`
**Polling:** 30s (catalog меняется редко), 10s (model/status)

**Primary API:** `/api/model/status`, `/api/model/catalog`
**Secondary API:** `/api/thinking/status`, `/api/model/local/status`, `/api/openclaw/routing/effective`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  🧠 Models & Routing          [Active: gemini-3-pro]  [Depth: medium]│
└──────────────────────────────────────────────────────────────────────┘

┌── CURRENT ROUTE ──────────────────────────────────────────────────────┐
│ Active Model:   gemini-3-pro-preview                                   │
│ Provider:       google                                                  │
│ Channel:        telegram                                                │
│ Thinking Depth: medium                                                  │
│ Effective Route: [expand json]                                          │
└────────────────────────────────────────────────────────────────────────┘

┌── THINKING DEPTH ──────────────────────────────────────────────────────┐
│ Current: medium                                                         │
│ [off] [minimal] [low] [medium ●] [high] [xhigh] [adaptive]            │
│ [Apply Depth]                                                           │
└─────────────────────────────────────────────────────────────────────────┘

┌── MODEL CATALOG ────────────────────────────────────────────────────────┐
│ Provider  │ Model                    │ Status  │ Actions                │
│ google    │ gemini-3-pro-preview  ★  │ ● ok    │ [Set Primary] [Probe]  │
│ google    │ gemini-3-flash-preview   │ ● ok    │ [Set Primary] [Probe]  │
│ google    │ gemini-2.5-pro-preview   │ ● ok    │ [Set Primary]          │
│ lmstudio  │ local-default            │ ○ idle  │ [Load] [Unload]        │
│ [Force Refresh Catalog]                                                  │
└─────────────────────────────────────────────────────────────────────────┘

┌── LM STUDIO ───────────────────────────────────────────────────────────┐
│ Status: idle  │  RAM: 12GB/36GB                                         │
│ [Load Default Model]  [Unload]                                          │
│ Note: одна модель за раз — RAM overflow на 36GB M4 Max!                 │
└─────────────────────────────────────────────────────────────────────────┘

┌── AUTOSWITCH ──────────────────────────────────────────────────────────┐
│ Status: enabled                                                          │
│ [Apply Autoswitch Rules]                                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

**Inline actions:**
- `[Set Primary]` → POST `/api/model/apply` body: `{"provider": "...", "model": "..."}`
- `[Probe]` → POST `/api/model/provider-action` body: `{"action": "probe", "provider": "..."}`
- `[Apply Depth]` → POST `/api/thinking/set` body: `{"mode": "medium"}`
- `[Force Refresh Catalog]` → GET `/api/model/catalog?force_refresh=true`
- `[Load Default Model]` → POST `/api/model/local/load-default`
- `[Unload]` → POST `/api/model/local/unload`

---

### 5.10 /provisioning — Provisioning

**Файл:** `src/web/provisioning.html`
**Polling:** нет (по запросу)

**Primary API:** `/api/provisioning/drafts`, `/api/provisioning/templates`

#### Wireframe

```
┌─ TOPBAR ─────────────────────────────────────────────────────────────┐
│  ⚙ Provisioning                                   [+ New Draft]      │
└──────────────────────────────────────────────────────────────────────┘

┌── TEMPLATES ──────────────────────────────────────────────────────────┐
│ Entity: [agent ▼]          [Load Templates]                            │
│ Templates: agent-basic | agent-swarm-role | agent-translator           │
└────────────────────────────────────────────────────────────────────────┘

┌── DRAFTS LIST ────────────────────────────────────────────────────────┐
│ Filter: [Status ▼]  [Limit: 20]                      [↻ Refresh]       │
│                                                                         │
│ Draft ID     │ Type  │ Name          │ Status  │ Actions                │
│ draft-001    │ agent │ my-new-agent  │ pending │ [Preview] [Apply]      │
│ draft-002    │ agent │ swarm-trader  │ applied │ [View]                 │
└─────────────────────────────────────────────────────────────────────────┘

┌── CREATE DRAFT ────────────────────────────────────────────────────────┐
│ Entity type: [agent ▼]                                                  │
│ Name: [........................]                                         │
│ Role: [........................]                                         │
│ Description: [.................................................]          │
│ [Create Draft]                                                           │
└─────────────────────────────────────────────────────────────────────────┘

┌── DRAFT PREVIEW (expandable) ──────────────────────────────────────────┐
│ После клика [Preview] — показывает diff JSON                            │
│ [Apply (confirm)] → POST /api/provisioning/apply/{draft_id}?confirm=true│
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Общие компоненты

### 6.1 Auto-refresh индикатор

Все страницы с polling показывают в top bar:
```
[↻ 10s]  — countdown до следующего refresh (JS setInterval)
         — при загрузке: spinning icon
         — при ошибке: красный цвет + "retry in 10s"
```

### 6.2 Error state

При ошибке fetch (network/500):
```
┌── ERROR ─────────────────────────────────────────────────────────────┐
│ ⚠ Failed to load data: Network error                                  │
│ Last successful: 09:47:32          [Retry Now]                        │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.3 Loading skeleton

При первой загрузке — skeleton cards (пульсирующие серые блоки) вместо данных.

### 6.4 Confirm dialog

Для деструктивных actions (delete, clear, prune):
```
┌── CONFIRM ──────────────────────────────────────────────────────────┐
│  ⚠ Are you sure?                                                     │
│  This action cannot be undone.                                       │
│  [Cancel]                           [Confirm]                        │
└──────────────────────────────────────────────────────────────────────┘
```
Реализация: custom modal (не browser `confirm()`).

### 6.5 Token input

В settings panel (иконка ⚙ в top bar):
```
Web Key: [................................] [Save]
```
Сохраняется в `localStorage` под ключом `krab_web_key`.
Подставляется в header `X-Krab-Web-Key` для всех write requests.

### 6.6 Toast notifications

После успешных/неуспешных actions:
```
bottom-right corner:
  ✓ Alert acknowledged         (green, 3s)
  ✗ Failed to set budget       (red, 5s)
  ℹ Model switch scheduled    (cyan, 3s)
```

---

## 7. Responsive + Mobile требования

### Breakpoints

```
Mobile:  < 640px
Tablet:  640px – 1024px
Desktop: > 1024px
```

### Mobile адаптации

| Элемент | Desktop | Mobile |
|---------|---------|--------|
| Sidebar | 220px fixed left | скрыт, hamburger overlay |
| Layout | 2-column grid | 1-column stack |
| Tables | full width | горизонтальный scroll |
| Navbar | sidebar | bottom tab bar (5 пунктов) |
| Cards | side by side | stacked |
| Top bar | full | compact (logo + hamburger only) |

### Bottom tab bar (mobile)

```
[ 🏠 Main ] [ 📬 Inbox ] [ 💰 Costs ] [ 🔍 Ops ] [ 🐝 Swarm ]
```
Фиксированный снизу, высота 56px.

### Touch targets

Все кнопки минимум 44×44px на мобайле.

---

## 8. Реал-тайм и polling

### Стратегия polling по страницам

| Страница | Endpoint | Интервал |
|----------|----------|----------|
| / (main) | `/api/runtime/summary` | 15s |
| / (main) | `/api/health/lite` | 10s |
| /costs | `/api/costs/report` | 10s |
| /inbox | `/api/inbox/status` + items | 10s |
| /swarm | `/api/swarm/task-board` + tasks | 15s |
| /translator | `/api/translator/status` | 10s |
| /ops | `/api/ops/alerts` | 5s |
| /ops | `/api/timeline` | 10s |
| /ops | `/api/ops/metrics` | 30s |
| /voice-console | `/api/transcriber/status` | 2s |
| /models | `/api/model/status` | 10s |
| /models | `/api/model/catalog` | 30s |
| /commands | нет | — |
| /provisioning | нет | — |

### Паттерн реализации

```javascript
// Стандартный polling pattern для каждой страницы:
async function fetchData() {
  try {
    const r = await fetch('/api/xxx');
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    renderData(data);
    setLastUpdated(new Date());
  } catch (e) {
    showError(e.message);
  }
}

fetchData(); // initial
const timer = setInterval(fetchData, INTERVAL_MS);

// Countdown display:
let countdown = INTERVAL_SEC;
const countdownTimer = setInterval(() => {
  countdown--;
  if (countdown <= 0) countdown = INTERVAL_SEC;
  updateCountdownUI(countdown);
}, 1000);
```

### WebSocket (будущее)

Для `/ops` live journal: если потребуется sub-second latency,
добавить `WS /ws/journal` (backend пока не реализован).
MVP: polling `/api/timeline?limit=50` каждые 10s — достаточно.

---

## 9. Auth

### Write endpoints

Защищены одним из способов:
1. Header `X-Krab-Web-Key: <token>`
2. Query param `?token=<token>`

Токен хранится в `localStorage['krab_web_key']`.

### JS helper

```javascript
function authHeaders() {
  const token = localStorage.getItem('krab_web_key') || '';
  return {
    'Content-Type': 'application/json',
    'X-Krab-Web-Key': token,
  };
}

async function writeApi(url, body = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}
```

### Read endpoints

Открыты (без auth в dev режиме). Нет необходимости в token для GET.

---

## 10. Inline actions — кнопки с side-effects

Сводная таблица всех action кнопок по страницам:

| Страница | Кнопка | HTTP | Endpoint | Confirm? |
|----------|--------|------|----------|---------|
| / | Health Recheck | POST | `/api/runtime/recover` | нет |
| / | Clear Session | POST | `/api/runtime/chat-session/clear` | да |
| / | Toggle Notify | POST | `/api/notify/toggle` | нет |
| / | Toggle Silence | POST | `/api/silence/toggle` | нет |
| / | Ack Alert | POST | `/api/ops/ack/{code}` | нет |
| /costs | Set Budget | POST | `/api/costs/budget` | нет (modal form) |
| /inbox | Ack item | POST | `/api/inbox/update` | нет |
| /inbox | Bulk Ack | POST | `/api/inbox/update` (×N) | да |
| /inbox | Remediate Stale | POST | `/api/inbox/stale-processing/remediate` | да |
| /inbox | Create Item | POST | `/api/inbox/create` | нет (modal form) |
| /swarm | Create Task | POST | `/api/swarm/tasks/create` | нет (modal form) |
| /swarm | Edit Priority | POST | `/api/swarm/task/{id}/priority` | нет |
| /swarm | Delete Task | DELETE | `/api/swarm/task/{id}` | да |
| /swarm | Toggle Listeners | POST | `/api/swarm/listeners/toggle` | нет |
| /swarm | Cleanup Artifacts | POST | `/api/swarm/artifacts/cleanup` | да |
| /translator | Change Language | POST | `/api/translator/lang` | нет (modal form) |
| /translator | Pause/Resume | POST | `/api/translator/session/action` | нет |
| /translator | Stop Session | POST | `/api/translator/session/action` | да |
| /translator | Quick Phrase | POST | `/api/translator/session/quick-phrase` | нет |
| /ops | Ack Alert | POST | `/api/ops/ack/{code}` | нет |
| /ops | Run Smoke Test | POST | `/api/diagnostics/smoke` | нет |
| /ops | Prune Old Data | POST | `/api/ops/maintenance/prune` | да |
| /ops | Runtime Recover | POST | `/api/runtime/recover` | да |
| /voice-console | Toggle Voice | POST | `/api/voice/toggle` | нет |
| /models | Set Primary | POST | `/api/model/apply` | нет |
| /models | Probe Provider | POST | `/api/model/provider-action` | нет |
| /models | Apply Depth | POST | `/api/thinking/set` | нет |
| /models | Load LM Studio | POST | `/api/model/local/load-default` | нет |
| /models | Unload LM Studio | POST | `/api/model/local/unload` | да |
| /commands | Update ACL | POST | `/api/userbot/acl/update` | нет (form) |
| /provisioning | Create Draft | POST | `/api/provisioning/drafts` | нет (form) |
| /provisioning | Apply Draft | POST | `/api/provisioning/apply/{id}?confirm=true` | да |

---

## 11. Порядок реализации

### Приоритет 1 — Foundation

1. **Дизайн-система** — `nano_theme.css` общий файл с CSS переменными, базовыми классами
2. **Shared layout** — sidebar + topbar как include/snippet (или copy-paste в каждый файл)
3. **`/`** — main dashboard (обновить существующий `index.html`)

### Приоритет 2 — Операционные страницы

4. **`/inbox`** — высокая operability value
5. **`/ops`** — мониторинг и alerts
6. **`/costs`** — финансы

### Приоритет 3 — Feature страницы

7. **`/swarm`** — создать с нуля (прототип неполный)
8. **`/translator`** — обновить существующий
9. **`/commands`** — новая страница

### Приоритет 4 — Advanced

10. **`/models`** — новая страница для model management
11. **`/voice-console`** — новая страница
12. **`/provisioning`** — новая страница

---

## Файловая структура (output)

```
src/web/
  index.html          — / (Main Dashboard)
  costs.html          — /costs
  inbox.html          — /inbox
  swarm.html          — /swarm
  translator.html     — /translator
  ops.html            — /ops (новая)
  voice-console.html  — /voice-console (новая)
  commands.html       — /commands (новая)
  models.html         — /models (новая)
  provisioning.html   — /provisioning (новая)
  nano_theme.css      — общая CSS тема
```

Backend routes для новых страниц нужно добавить в `src/modules/web_app.py`:

```python
@self.app.get("/ops", response_class=HTMLResponse)
async def ops_page(): return FileResponse("src/web/ops.html")

@self.app.get("/voice-console", response_class=HTMLResponse)
async def voice_console_page(): return FileResponse("src/web/voice-console.html")

@self.app.get("/commands", response_class=HTMLResponse)
async def commands_page(): return FileResponse("src/web/commands.html")

@self.app.get("/models", response_class=HTMLResponse)
async def models_page(): return FileResponse("src/web/models.html")

@self.app.get("/provisioning", response_class=HTMLResponse)
async def provisioning_page(): return FileResponse("src/web/provisioning.html")
```

---

## Технические замечания для Gemini

1. **Без фреймворков** — только vanilla JS (ES2022+), fetch API, CSS custom properties
2. **Inline всё** — каждый HTML файл self-contained (CSS + JS inline или `<link>` к nano_theme.css)
3. **Нет зависимостей** — никаких npm, CDN (кроме Google Fonts для Outfit если нужен)
4. **Accessibility** — ARIA labels на интерактивных элементах, keyboard navigation
5. **Encoding** — UTF-8, русский текст допустим в UI
6. **Error handling** — каждый fetch должен иметь try/catch с показом ошибки пользователю
7. **Server** — `http://127.0.0.1:8080`, не нужен CORS proxy
8. **Auth token** — хранить в `localStorage['krab_web_key']`, подставлять в header при write

---

_Сгенерировано: 12.04.2026. Автор: Claude Code (session 7 subagent)._
_На основе анализа: `src/modules/web_app.py` (13811 строк, ~190 endpoints), `docs/DASHBOARD_SPEC.md`._
