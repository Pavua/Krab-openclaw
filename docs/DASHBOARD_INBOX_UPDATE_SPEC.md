# Dashboard: /inbox Page Update Spec — Gemini 3.1 Pro

> Обновление /inbox page с актуальными API endpoints

## API Endpoints

```
GET /api/inbox/status  → {total_items, open_items, attention_items, ...}
GET /api/inbox/items?limit=20&status=open  → {items: [...]}
```

## UI Layout

### Header
- "📬 Inbox" заголовок
- Status badges: Open (N), Attention (N), Escalations (N)

### Секция: Summary Cards
- Total items
- Fresh open vs stale open
- Pending reminders / approvals / owner requests

### Секция: Item List
- Таблица: severity (emoji) | title | kind | source | created_at
- Severity: info=ℹ️, warning=⚠️, error=🔴, critical=🚨
- Filter by status: open / acked / all
- Click to expand body text

### Секция: Quick Actions
- Ack item button
- Done / Cancel buttons
- Bulk ack open items

## Gemini Prompt

```
Создай HTML страницу /inbox для Krab dashboard.

Данные из API:
- GET /api/inbox/status → summary counts
- GET /api/inbox/items?limit=20&status=open → item list

Layout:
1. Навбар: ← / | Stats | Inbox (active) | Costs | Swarm | Translator
2. Summary cards: total, open, attention, escalations
3. Item list table: severity | title | kind | source | created_at
4. Expandable body on click
5. Auto-refresh 10s

Стиль: тёмная тема (#1a1a2e), карточки, responsive.
Severity emoji: info=ℹ️, warning=⚠️, error=🔴
```
