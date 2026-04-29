# Observability Audit — Krab Session 19 (2026-04-23)

Дата проверки: 2026-04-23 ~19:45 UTC

## Статус-таблица

| Система | Жива? | Events за 24h | Рекомендации |
|---------|-------|---------------|--------------|
| **Sentry** | ДА — DSN установлен, SDK инициализирован при каждом старте | 8 live issues (4 типа) | Перевести `environment` из `dev` в `production`; включить `traces_sample_rate=1.0` для LLM-flow (сейчас 10%) |
| **Prometheus** | ДА — `/metrics` отдаёт 19 TYPE-строк, валидный text/plain | N/A (pull, не push) | Внешний Prometheus-scraper **не запущен** — данные никуда не улетают. Запустить `prometheus --config.file=docs/prometheus.yml` или добавить в LaunchAgents |
| **Linear** | ЧАСТИЧНО — один проект (Session 16), 4 issues за месяц | 0 за 24h | Связи Sentry→Linear нет; AGE-8 (`subprocess.run` blocking event loop) висит в Backlog без сроков |
| **Proactive Watch** | ДА — loop зарегистрирован, alert cooldown 30 мин, Error Digest каждые 6h | Не видно alert'ов в inbox за 24h | Работает штатно; 27 open inbox items (24 stale) требуют ревью |

---

## 1. Sentry — детальный разбор

### Инициализация

- `init_sentry()` вызывается в `src/main.py:66` при каждом старте.
- Логи подтверждают успешный `sentry_initialized` **11 раз за 22-23 апреля** (часто из-за перезапусков в рамках разработки).
- DSN: `https://...@o4511079566278656.ingest.de.sentry.io/...` — регион DE, проект `po-zm/krab`.
- Sample rates: `traces=0.1`, `profiles=0.1` (10%) — разумно для prod, но в dev стоит повысить до 1.0 для полноты.
- `before_send` hook активен: PII-redact (токены, телефоны, Bearer) + фильтр pyrogram shutdown noise.
- `include_local_variables=False`, `send_default_pii=False` — безопасно.
- Интеграции: `FastApiIntegration`, `AsyncioIntegration`, `LoggingIntegration(event_level=ERROR)`.

### Ошибки за последние 24 часа (8 issues)

| ID | Тип | Events | Первый | Последний |
|----|-----|--------|--------|-----------|
| PYTHON-FASTAPI-4W | `memory_indexer_embed_failed` — SQLite cross-thread | 2 | ~3h ago | ~1h ago |
| PYTHON-FASTAPI-4Y | `memory_indexer_embed_failed` — SQLite cross-thread | 2 | ~4h ago | ~1h ago |
| PYTHON-FASTAPI-Z | `Traceback` (generic) | 2 | ~16h ago | ~1h ago |
| PYTHON-FASTAPI-5H | `openclaw_api_error` — internal error 500 | 2 | ~3h ago | ~3h ago |
| PYTHON-FASTAPI-5G | `openclaw_api_error` — internal error 500 | 1 | ~4h ago | ~4h ago |
| PYTHON-FASTAPI-5F | `openclaw_health_check_failed` | 1 | ~16h ago | ~16h ago |
| PYTHON-FASTAPI-53 | `openclaw_health_check_failed` — All connection attempts failed | 4 | ~20h ago | ~17h ago |
| PYTHON-FASTAPI-5 | `openclaw_health_check_failed` — All connection attempts failed | 1 | ~22h ago | ~22h ago |

**Топ-проблема**: `memory_indexer_embed_failed` — SQLite объекты созданы в одном thread, используются в другом (`thread id 6295482368` → `6194524160`). Воспроизводится стабильно (см. logs 16:44, 19:11, 19:28). Это root cause, а не symptom — связано с Linear issue AGE-8.

**Важное наблюдение**: `capture_exception()` и `capture_message()` нигде не вызываются вручную в src/ — Sentry собирает ошибки **только через `LoggingIntegration(event_level=ERROR)`** и AsyncioIntegration. Ручных инструментаций нет.

### Конфигурация (config.py)

```python
SENTRY_DSN: str = os.getenv("SENTRY_DSN", "").strip()
KRAB_ENV: str = os.getenv("KRAB_ENV", "dev")   # <-- всегда "dev" если не переопределено
SENTRY_TRACES_SAMPLE_RATE: float = 0.1
SENTRY_PROFILES_SAMPLE_RATE: float = 0.1
```

**Gap**: `KRAB_ENV` defaults to `"dev"` — все события в Sentry помечены `environment=dev`. Для production-мониторинга нужно `KRAB_ENV=production` в `.env`.

---

## 2. Prometheus — детальный разбор

### Endpoint

`GET http://127.0.0.1:8080/metrics` — ЖИВ, отвечает валидным Prometheus text/plain.

### Активные метрики (19 TYPE, ~117 строк вывода)

| Метрика | Тип | Текущее значение |
|---------|-----|-----------------|
| `krab_archive_messages_total` | gauge | 753 049 |
| `krab_archive_chats_total` | gauge | 878 |
| `krab_archive_chunks_total` | gauge | 72 318 |
| `krab_archive_db_size_bytes` | gauge | 530 796 544 (~506 МБ) |
| `krab_llm_route_ok{provider="codex-cli",model="codex-cli/gpt-5.4"}` | gauge | 1 (OK) |
| `krab_command_invocations_total` | counter | bench:6, status:6, uptime:4, swarm:4 |
| `krab_memory_validator_*` | counter/gauge | все 0 |
| `krab_reminders_pending_total` | gauge | 0 |
| `krab_memory_adaptive_rerank_used_total` | counter | (живой счётчик) |
| `krab_chat_windows_active` | gauge | (live) |
| `krab_metrics_generated_at` | gauge | Unix timestamp |

**Реализация**: hand-rolled text exposition без `prometheus_client`, что корректно и без лишних зависимостей.

### Внешний scraper

**НЕ ЗАПУЩЕН.** `prometheus` процесс не найден. Конфиг `docs/prometheus.yml` готов, алерты `docs/krab_alerts.yml` описаны (6 правил), но данные никуда не экспортируются — нет ни Prometheus, ни Grafana, ни alertmanager.

**Замечание**: `krab_archive_db_size_bytes = 530 МБ` — выше порога `ARCHIVE_DB_WARN_MB=500`. Алерт в inbox должен был сработать через `proactive_watch._check_archive_db_size()` с cooldown 12h.

---

## 3. Linear — детальный разбор

### Состояние

- Workspace: `agentsss`, команда: `Agents`
- Активный проект: **Krab Session 16 — Wave 4 + Memory + Ops V4**
- Issues за последний месяц: **4** (AGE-5, AGE-6, AGE-7, AGE-8)
- За последние 24h новых issues: **0**

### Связь с Sentry

**Связи Sentry→Linear нет.** Issues создаются только вручную. Нет ни webhook, ни автоматического тикетирования из Sentry alerts. Sentry MCP (`mcp__sentry__`) доступен через Claude Desktop, но не подключён к Linear-проекту.

### Открытые issues

| ID | Заголовок | Приоритет | Статус |
|----|-----------|-----------|--------|
| AGE-8 | `subprocess.run` blocking event loop in `memory_doctor.run_repairs()` | High | Backlog (без срока) |

AGE-8 **напрямую связан** с текущими Sentry ошибками (`memory_indexer_embed_failed` — SQLite cross-thread). Оба симптома указывают на проблему с thread-safety в memory layer.

---

## 4. Proactive Watch — детальный разбор

### Архитектура

`ProactiveWatchService` в `src/core/proactive_watch.py`:
- `capture()` — snapshot gateway/model/scheduler/macOS, persist в `~/.openclaw/krab_runtime_state/proactive_watch_state.json`
- `run_error_digest()` — каждые 6 часов, пишет в inbox
- `run_alert_checks()` — каждые 30 минут: inbox_critical, swarm_job_stalled, cost_budget, archive_db_size
- `run_auto_restart_checks()` — каждые 5 минут: health probe 4 сервисов
- `start_weekly_digest_loop()` — каждые 7 дней

### Статус (из `/api/health/lite`)

- Userbot: **running**, Telegram client: **connected**
- Inbox: 200 total, 27 open (24 stale), 5 attention items, 4 pending owner requests
- Scheduler: enabled
- Route: `codex-cli/gpt-5.4` — OK

### Замечания

- 27 open inbox items, из них 24 stale — нуждаются в ручном `!inbox list` и triage
- Proactive Watch нотификации в DM работают через `notifier` callback, но **только при `notify=True`** — нужно проверить что фоновый цикл вызывается с `notify=True`

---

## 5. Missing Observability — что стоит добавить

| Что | Почему важно | Сложность |
|-----|-------------|-----------|
| **`KRAB_ENV=production` в .env** | Все Sentry события сейчас в `dev` — нельзя настроить production-алерты | Тривиально |
| **Ручные `capture_exception()` в critical paths** | LLM flow ошибки, openclaw_api_error, memory_indexer — сейчас собираются только через LoggingIntegration | Низкая |
| **Запуск Prometheus LaunchAgent** | Метрики не тарятся — нет истории, нет графиков, нет alertmanager | Средняя |
| **SQLite cross-thread fix в memory_indexer** | Стабильная ошибка 2+ раз/день в Sentry — Linear AGE-8 в Backlog | Средняя |
| **Sentry→Linear webhook** | Автоматическое создание тикетов из ошибок Sentry | Средняя |
| **`openclaw_stream_edit_delivery_failed` тикет** | Ошибка `403 MESSAGE_AUTHOR_REQUIRED` повторяется 4+ раз/день, не тикетирована | Низкая |
| **Метрика `krab_sentry_events_total`** | Prometheus не знает о Sentry — нет cross-system корреляции | Низкая |

---

## Итог

**Sentry** — полностью жив, события реально приходят (8 issues за 24h). Основная дырка: `KRAB_ENV=dev` вместо `production`.

**Prometheus** — endpoint работает, 19 метрик, но внешний scraper не запущен → данные живут только в `/metrics` и никуда не идут. Надо запустить Prometheus daemon.

**Linear** — используется вручную как issue-трекер для сессионных задач. Нет auto-интеграции с Sentry. AGE-8 (blocking subprocess) актуален и связан с текущими Sentry-ошибками.

**Proactive Watch** — работает, loops запущены, inbox наполняется. 24 stale items требуют тriage.
