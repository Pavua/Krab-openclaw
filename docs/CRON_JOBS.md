# Krab Cron Jobs (native scheduler)

Файл хранения: `~/.openclaw/krab_runtime_state/cron_native_jobs.json`
Исполнитель: `src/core/cron_native_scheduler.py` (fallback когда OpenClaw CLI недоступен).
Схема: `{version: 1, jobs: [{id, cron_spec, prompt, enabled, created_at, last_run_at, run_count}]}`.

Все jobs крутятся относительно локальной timezone хоста. Cron-формат стандартный 5-полевой (M H D Mo Dow). `NO_REPLY` в prompt'е означает, что агент должен промолчать если условие не сработало (не спамить Saved Messages каждый раз).

## Active jobs

### 1. `daily-morning-brief` — `0 8 * * *`

Ежедневно в 08:00. Утренний brief в Saved Messages:
- Топ-5 чатов с ночными сообщениями (>3 msgs)
- Вчерашние расходы vs дневной budget
- Новые inbox items со статусом `open`

**Rationale:** быстрый morning check-in без необходимости руками гонять `!catchup` + `!costs` + `!inbox` по утрам. Формат: 3-5 строк — чтобы читалось за 10 секунд с телефона.

### 2. `daily-evening-recap` — `0 22 * * *`

Ежедневно в 22:00. Вечерний recap:
- Summary ключевых событий дня из history
- Завтрашние reminders из scheduler
- Выполненные swarm tasks

**Rationale:** закрытие дня — что успели, что ждёт завтра. Формат 4-6 строк. Помогает планировать утро следующего дня.

### 3. `archive-growth-alert-6h` — `0 */6 * * *`

Каждые 6 часов (00:00, 06:00, 12:00, 18:00). Alert если `archive.db` вырос >500MB за период.

**Rationale:** Memory Layer Phase 1 live, archive.db уже ~51MB (19.04.2026). Growth spike обычно = memory leak или spam в мониторинге. Ранее wave 29 зафиксирован blocker по sqlite-vec malformed — такие инциденты хорошо видны по growth rate. Молчит если норма — не шумит.

### 4. `cost-budget-midday-check` — `0 13 * * *`

Ежедневно в 13:00. Alert если >80% дневного budget потрачено.

**Rationale:** FinOps safeguard. К полудню (после morning swarm runs + research pipeline) обычно уходит половина дневного budget — если >80%, значит есть runaway (напр. бесконечный tool loop или плохо сконфигурированный Gemini 3 Pro). Раннее предупреждение до ночных cron-задач, которые дожрут остаток.

## Управление

```bash
# Список
python -c "from src.core.cron_native_store import list_jobs; print(list_jobs())"

# Через owner panel
curl http://127.0.0.1:8080/api/openclaw/cron/jobs
curl -X POST http://127.0.0.1:8080/api/openclaw/cron/jobs/toggle -d '{"job_id":"...","enabled":false}'

# Telegram
!cron list
!cron toggle <id>
```

Runtime автоматически pickup'ит изменения JSON файла при следующем tick scheduler'а — рестарт Krab не нужен.
