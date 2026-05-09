# Phase 7 — Service Workflows: анализ текущего состояния

> Составлено: 2026-04-12 (session 6 research)
> Аналитик: Claude Sonnet 4.6

---

## 1. Что такое Phase 7 в контексте Krab

Phase 7 — это слой "service workflows": автоматические отчёты, research pipelines и scheduled-задачи, которые запускаются автономно (без ручного `!swarm`), сохраняют результаты и/или уведомляют owner-а. Упомянута в `.remember/next_session.md` как приоритет сессии 6: "expand auto-reports, research pipeline".

В коде Phase 7 явно упоминается только в двух местах:
- `src/core/swarm.py:336` — комментарий "Phase 7: auto-save markdown report для analysts/research rounds"
- `src/core/swarm_artifact_store.py:100` — docstring метода `save_report()`

---

## 2. Что уже реализовано (фундамент Phase 7)

### 2.1 swarm_scheduler.py — RecurringJob engine
**Файл:** `src/core/swarm_scheduler.py`

Полностью рабочий рекуррентный планировщик. Ключевые возможности:
- `add_job(team, topic, interval_sec)` — создаёт рекуррентный job
- `_run_job_loop()` — бесконечный sleep→run→repeat цикл
- `_execute_job()` — запускает `AgentRoom.run_round()`, сохраняет в swarm_memory, отправляет owner-у
- Persist в `~/.openclaw/krab_runtime_state/swarm_recurring_jobs.json`
- Команды: `!swarm schedule <team> <topic> <interval>`, `!swarm jobs`, `!swarm unschedule <id>`
- Гейт `SWARM_AUTONOMOUS_ENABLED` (дефолт: false)
- **Ограничение:** только один уровень — "каждые N часов, одна команда, одна тема". Нет типов задач (report vs research vs alert).

### 2.2 swarm_artifact_store.py — Phase 7 persistence layer
**Файл:** `src/core/swarm_artifact_store.py`

- `save_round_artifact()` — JSON-артефакт каждого раунда в `swarm_artifacts/`
- `save_report()` — markdown-отчёт в `reports/` (Phase 7 hook, вызывается из swarm.py для analysts/traders)
- `list_artifacts()`, `get_artifact()`, `cleanup_old()`
- Singleton `swarm_artifact_store`

### 2.3 Auto-save в swarm.py
**Файл:** `src/core/swarm.py:336-342`

При завершении раунда для команд `analysts` и `traders` автоматически вызывается `save_report()`. Для `coders` и `creative` — только JSON-артефакт (без markdown-отчёта). **Это и есть "Phase 7 started" из session 5.**

### 2.4 proactive_watch.py — мониторинг runtime
**Файл:** `src/core/proactive_watch.py`

- `collect_snapshot()` — снимает gateway/routing/scheduler/macOS состояние
- `capture(manual, notify, notifier)` — diff с baseline, пишет в workspace memory, уведомляет при изменениях
- `_check_and_trace_cron_executions()` — трейсит OpenClaw cron jobs через inbox_service
- `_detect_reason()` — детектирует: gateway_down/recovered, route_model_changed, scheduler_backlog_created/cleared, frontmost_app_changed
- Triggered фоново из `userbot_bridge.py:843` с интервалом `PROACTIVE_WATCH_INTERVAL_SEC` (default: 900 сек)
- **Ограничение:** только мониторит runtime state, не выполняет никаких workflow-действий.

### 2.5 swarm_task_board.py — задачи
**Файл:** `src/core/swarm_task_board.py`

- Хранит 200 задач, FIFO
- `create_task()`, `complete_task()`, `fail_task()`, `assign_task()`
- Команды: `!swarm task board/list/create/done/fail/assign`
- Авто-создание задачи при каждом swarm round (из `swarm.py:361`)
- API endpoint `/api/swarm/tasks`

### 2.6 Reports list endpoint
**Файл:** `src/modules/web_app.py:9460`

`GET /api/swarm/reports` — список markdown-файлов из `reports/` dir. Есть. Работает.

---

## 3. Что НЕ сделано (gaps)

### GAP 1: Нет встроенных workflow-шаблонов
Сейчас owner создаёт job вручную: `!swarm schedule analysts "BTC анализ" 4h`. Нет предустановленных типовых workflow с осмысленными промптами и настройками. Каждый job — просто "произвольная тема для произвольной команды".

### GAP 2: Нет weekly summary
Нет job, который раз в неделю собирает и сводит всё произошедшее за 7 дней: swarm-раунды, задачи, ошибки, cost stats, model switches.

### GAP 3: Нет error digest
proactive_watch.py детектирует `gateway_down` и `route_model_changed`, но нет агрегированного периодического дайджеста ошибок: inbox items со статусом open/failed, swarm job failures, inbox по severity.

### GAP 4: Нет research pipeline templates
Нет шаблона "глубокое исследование": analysts → web_search × N → структурированный отчёт → сохранить → переслать. Сейчас analysts сохраняет отчёт, но без web-поиска как обязательного шага.

### GAP 5: Нет auto-dispatch по OpenClaw cron
OpenClaw уже имеет cron (Daily Morning Report, Mercadona Restock, jokes). proactive_watch их трейсит, но они не связаны со swarm. Нет механизма "когда OpenClaw cron выполнился → запустить swarm job на основе его результата".

### GAP 6: swarm reports — только analysts/traders
`swarm.py:337` — `save_report()` вызывается только для `analysts` и `traders`. Команды `coders` и `creative` не генерируют markdown-отчёты, только JSON-артефакты.

### GAP 7: Нет Alert workflows
proactive_watch отправляет owner-у текстовый digest при gateway_down, но нет:
- Алертов при накоплении N ошибок за M минут
- Алертов при превышении cost budget
- Алертов при длительном молчании swarm jobs (job завис/не запускался)

### GAP 8: Нет отчёта "состояние за сессию"
Нет механизма, который при shutdown Краба (или по команде) выдаёт сводку: что было сделано за эту сессию (задачи, swarm rounds, cost).

---

## 4. Конкретные предложения по расширению

### P0: WeeklyDigest job (1-2ч, высокая ценность)

**Файл:** новый `src/core/swarm_weekly_digest.py`

Что делает:
1. Раз в 7 дней собирает из swarm_artifact_store все артефакты за неделю
2. Берёт stats из cost_analytics
3. Берёт inbox items (failed, open)
4. Формирует markdown `weekly_digest_YYYY-MM-DD.md` в `reports/`
5. Отправляет owner-у в Telegram

API:
```python
async def generate_weekly_digest(sender: Callable, owner_chat_id: str) -> str:
    """Собирает weekly digest и возвращает markdown."""
```

Команда: `!swarm digest` (ручной триггер), `!swarm digest weekly` (показать последний)

Интеграция: добавить в `swarm_scheduler.py` тип `"digest"` или зарегистрировать в OpenClaw cron.

**Ориентировочные файлы:**
- Создать: `src/core/swarm_weekly_digest.py`
- Изменить: `src/handlers/command_handlers.py` (добавить `!swarm digest`)
- Изменить: `src/modules/web_app.py` (добавить `GET /api/swarm/digest/latest`)

---

### P0: ErrorDigest в proactive_watch (30мин, высокая ценность)

**Файл:** `src/core/proactive_watch.py`

Добавить в `ProactiveWatchService`:
- Метод `collect_error_digest()` — агрегирует: swarm job failures за последние 24ч, inbox items с severity="warning"/"critical", route_model_changed события
- Периодический trigger каждые 6ч (отдельный job в krab_scheduler)
- Если ошибок > threshold → отправить owner-у compact digest

**Изменения только в существующих файлах:**
- `src/core/proactive_watch.py` — добавить метод + dataclass `ErrorDigestSnapshot`
- `src/userbot_bridge.py` — зарегистрировать periodic task

---

### P1: Research Pipeline Template (2-3ч)

**Файл:** новый `src/core/swarm_research_pipeline.py`

Что делает:
1. Принимает `research_topic` и `search_queries: list[str]`
2. Запускает analysts команду с системным промптом, обязывающим использовать web_search
3. После раунда — сохраняет структурированный отчёт с секциями: Summary / Key Findings / Sources / Next Steps
4. Возвращает path к отчёту

Команда: `!swarm research <тема>` → запускает research pipeline (не просто `!swarm analysts <тема>`)

**Файлы:**
- Создать: `src/core/swarm_research_pipeline.py`
- Изменить: `src/handlers/command_handlers.py` — добавить `research` sub-command
- Изменить: `src/core/swarm_artifact_store.py` — добавить `save_research_report()` с отдельной директорией `research_reports/`

---

### P1: Scheduled Auto-Dispatch (1ч, расширение swarm_scheduler)

**Файл:** `src/core/swarm_scheduler.py`

Добавить в `RecurringJob`:
- `workflow_type: str` — "swarm_round" (текущее) | "research" | "digest" | "alert_check"
- `cron_expr: str | None` — опциональный cron expression вместо interval (для точного "каждый понедельник в 09:00")

Расширить `add_job()` и `_execute_job()` для ветвления по `workflow_type`.

Команда: `!swarm schedule analysts "BTC research" weekly monday 09:00` → создаёт weekly research job.

---

### P2: Swarm Session Summary (30мин)

**Файл:** `src/handlers/command_handlers.py`

Команда `!swarm summary` (или автоматически при `!stop`/shutdown):
- Читает swarm_task_board — сколько задач создано/completed/failed за текущую сессию (от startup timestamp)
- Читает swarm_artifact_store — сколько артефактов сохранено
- Берёт cost_analytics — потрачено токенов/USD за сессию
- Формирует compact text digest и отправляет owner-у

**Файлы:**
- Изменить: `src/handlers/command_handlers.py` (добавить `!swarm summary`)
- Изменить: `src/userbot_bridge.py` (опционально: вызов при shutdown)

---

### P2: Alert Workflows (1.5ч)

**Файл:** `src/core/proactive_watch.py`

Добавить в `_detect_reason()`:
- `cost_budget_exceeded` — если `cost_analytics.check_budget_ok()` вернул False
- `swarm_job_stalled` — если job не запускался > 2× от своего interval_sec
- `inbox_critical_open` — если inbox items с severity="critical" и status="open" > 0

Расширить `open_trace_reasons` и `close_trace_reasons` для автоматического inbox lifecycle.

---

### P2: Full coverage — save_report для всех команд (15мин)

**Файл:** `src/core/swarm.py:337`

Заменить:
```python
if _team_name in {"analysts", "traders"}:
```
на:
```python
if _team_name in {"analysts", "traders", "coders", "creative"}:
```

Все команды должны генерировать markdown-отчёты, а не только analysts/traders. Минимальное изменение, максимальная польза для Phase 7.

---

## 5. Приоритеты

| Приоритет | Задача | Усилие | Файлы |
|-----------|--------|--------|-------|
| **P0** | WeeklyDigest job | 1-2ч | `swarm_weekly_digest.py` (новый), `command_handlers.py` |
| **P0** | ErrorDigest в proactive_watch | 30мин | `proactive_watch.py`, `userbot_bridge.py` |
| **P1** | Research Pipeline Template | 2-3ч | `swarm_research_pipeline.py` (новый), `command_handlers.py` |
| **P1** | Scheduled Auto-Dispatch (workflow_type + cron) | 1ч | `swarm_scheduler.py` |
| **P2** | Swarm Session Summary (`!swarm summary`) | 30мин | `command_handlers.py` |
| **P2** | Alert Workflows (cost/stalled/critical) | 1.5ч | `proactive_watch.py` |
| **P2** | save_report для всех команд | 15мин | `swarm.py:337` |

---

## 6. Зависимости и риски

- **WeeklyDigest** зависит от `cost_analytics.build_usage_report_dict()` и `inbox_service` — оба существуют, риск низкий
- **Research Pipeline** требует чтобы `SWARM_AUTONOMOUS_ENABLED=1` и доступ web_search через MCP — гейт уже есть
- **Scheduled Auto-Dispatch с cron** требует парсера cron expressions — можно использовать `croniter` (уже в зависимостях?) или простой "dayofweek + time" парсер
- **Alert Workflows** — нужно проверить что `cost_analytics` singleton доступен из `proactive_watch.py` без circular import

Текущий blocker Phase 7: ничего критического. Все необходимые строительные блоки (scheduler, artifact_store, task_board, proactive_watch, inbox_service) уже существуют и работают. Phase 7 — это оркестровка поверх них.

---

## 7. Итог: статус Phase 7

**Готовность: ~25%**

- ✅ Foundation: swarm_scheduler, swarm_artifact_store, proactive_watch, swarm_task_board
- ✅ Auto-save reports для analysts/traders
- ✅ List reports endpoint (`/api/swarm/reports`)
- ✅ `!swarm report` команда (показывает последние 5 отчётов)
- ❌ WeeklyDigest — нет
- ❌ ErrorDigest — нет
- ❌ Research Pipeline Template — нет
- ❌ Scheduled Auto-Dispatch с workflow types — нет
- ❌ Session Summary — нет
- ❌ Alert Workflows — нет
