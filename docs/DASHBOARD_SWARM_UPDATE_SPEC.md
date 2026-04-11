# Dashboard: /swarm Page Update Spec — Gemini 3.1 Pro

> Обновление /swarm page с новыми Phase 8 данными (task board, artifacts, listeners)

## API Endpoints

### Существующие
- `GET /api/swarm/status` — memory + teams

### Новые (session 5)
```
GET /api/swarm/task-board    → {summary: {by_status, by_team, total}}
GET /api/swarm/tasks?team=&limit=20  → {tasks: [{task_id, team, title, status, priority, created_at}]}
GET /api/swarm/artifacts?team=&limit=10  → {artifacts: [{team, topic, timestamp_iso, duration_sec, result_preview}]}
GET /api/swarm/listeners     → {listeners_enabled: bool}
POST /api/swarm/tasks/create → {ok, task_id, team, title}
```

## UI Layout

### Секция: Task Board
- Summary badges: pending (⏳), in_progress (🔄), done (✅), failed (❌)
- By team breakdown
- Task list table: id | team | title | status | priority | created

### Секция: Artifacts
- Last 10 round artifacts
- Team | Topic | Duration | Timestamp
- Expandable result preview

### Секция: Team Listeners
- Status badge: ON/OFF
- Team accounts list: @p0lrdp_AI, @p0lrdp_worldwide, @hard2boof, @opiodimeo

## Gemini Prompt

```
Обнови HTML страницу /swarm для Krab dashboard.

Добавь 3 новых секции:
1. Task Board — summary badges (pending/in_progress/done/failed), by team,
   task list table
2. Artifacts — last 10 round artifacts with team/topic/duration
3. Team Listeners — ON/OFF toggle badge, team accounts list

Данные из API:
GET /api/swarm/task-board, GET /api/swarm/tasks, GET /api/swarm/artifacts,
GET /api/swarm/listeners

Стиль: тёмная тема (#1a1a2e), карточки, как остальные pages.
Использовать fetch + auto-refresh каждые 10 сек.
```
