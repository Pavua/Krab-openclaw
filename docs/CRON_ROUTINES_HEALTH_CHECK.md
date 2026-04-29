# Cron Routines Health Check

**Дата:** 2026-04-24
**Ветка:** `fix/daily-review-20260421`
**Конфиг:** `~/.openclaw/krab_runtime_state/cron_native_jobs.json`
**Scheduler:** `src/core/cron_native_scheduler.py` (singleton `cron_native_scheduler`)

## TL;DR — критическая находка

**Все 4 cron job'а являются silent no-op.** Scheduler стартует, тикает, логирует
`cron_native_job_firing` / `cron_native_job_done` и обновляет `last_run_at`,
но промпт **никогда не доставляется** ни в Telegram, ни в LLM, ни в Saved Messages.

**Root cause:** `cron_native_scheduler.bind_sender(...)` не вызывается нигде в
кодовой базе. В `_run_job()` есть guard:

```python
if self._sender:
    await self._sender("cron_native", prompt)
cron_native_store.mark_run(job_id)   # выполняется всегда
```

`self._sender` остаётся `None` → prompt тихо игнорируется, `mark_run()` всё равно
отрабатывает, создавая иллюзию успешного запуска.

Поиск: `grep -r "cron_native_scheduler.bind_sender" src/` → **0 matches**.
Сравните с рабочим `krab_scheduler.bind_sender(...)` в `userbot_bridge.py:1780`.

## Статус по jobs

| Job ID | Schedule | run_count | last_run_at | Status | Проблема |
|---|---|---|---|---|---|
| `daily-morning-brief` | `0 8 * * *` | 1 | 2026-04-24T08:00:00Z | ❌ Silent no-op | sender не bound; «done» без доставки |
| `daily-evening-recap` | `0 22 * * *` | 0 | — | ⏳ Не сработал | Ещё не наступило время (22:00) |
| `archive-growth-alert-6h` | `0 */6 * * *` | 3 | 2026-04-24T18:00:00Z | ❌ Silent no-op | Пропустил критическое событие (см. ниже) |
| `cost-budget-midday-check` | `0 13 * * *` | 1 | 2026-04-24T13:00:00Z | ❌ Silent no-op | sender не bound |

### Подтверждение из логов (`~/.openclaw/krab_runtime_state/krab_main.log`)

```
2026-04-24 08:00:00 info cron_native_job_firing  job_id=archive-growth-alert-6h
2026-04-24 08:00:00 info cron_native_job_done    job_id=archive-growth-alert-6h
2026-04-24 10:00:00 info cron_native_job_firing  job_id=daily-morning-brief
2026-04-24 10:00:00 info cron_native_job_done    job_id=daily-morning-brief
2026-04-24 15:00:00 info cron_native_job_firing  job_id=cost-budget-midday-check
2026-04-24 15:00:00 info cron_native_job_done    job_id=cost-budget-midday-check
2026-04-24 20:00:00 info cron_native_job_firing  job_id=archive-growth-alert-6h
2026-04-24 20:00:00 info cron_native_job_done    job_id=archive-growth-alert-6h
```

Latency между firing → done = **0 секунд** (ещё одно доказательство, что полезной
работы не выполняется; реальный LLM-запрос занял бы единицы секунд).

Sample output: **нет output'а ни для одного job** — ни одно сообщение не
опубликовано в Saved Messages. Проверяется косвенно: нет `_send_scheduled_message`
/ Telegram send logs, связанных с `chat_id="cron_native"`.

### Пропущенный alert (smoking gun)

`daily_maintenance.log` показывает что `archive.db` вырос **51 MB → 472 MB за сутки**
(20→22 апреля, +421 MB). Job `archive-growth-alert-6h` должен был предупредить
при росте >500 MB/6h — но даже если бы порог сработал, сообщение не дошло бы,
потому что sender не bound.

## Вторичная проблема: scheduler restart storm

`grep -c cron_native_scheduler_started`: **157**, `_stopped`: **126**.
Scheduler стартует/останавливается каждые ~30 секунд (видно в логах с 23:21:08
и далее — 20+ циклов за 15 минут). Вероятно hot-reload / watchdog. Это не
мешает cron'у напрямую (тики раз в 30с достаточно для минутной гранулярности),
но засоряет логи и создаёт окна, когда scheduler дышит, а не работает.

## Рекомендации по jobs

### 1. `daily-morning-brief` — ❌
- **Sender:** привязать `bind_sender` к функции, которая:
  - Отправляет `prompt` в `openclaw_client` / AgentRoom.
  - Результат публикует в Saved Messages (chat_id = self.me.id) через pyrogram.
- **Prompt:** ок, но конкретизировать источники: inbox endpoint
  `/api/inbox/items?status=open`, cost — `/api/costs/report?date=yesterday`.
- **Guard:** если источников данных нет (нет ночных msgs / нет трат) — вернуть
  `NO_REPLY` вместо пустого brief.

### 2. `daily-evening-recap` — ⏳ (ещё не сработал)
- **Sender:** тот же fix что и #1.
- **Prompt:** добавить явные инструменты — `swarm task list --status=done --since=24h`,
  `scheduler list tomorrow`, `history summary 24h`.
- **Schedule:** 22:00 может совпадать с активностью владельца → рассмотреть 23:30.

### 3. `archive-growth-alert-6h` — ❌ (критично)
- **Sender:** та же проблема.
- **Улучшение prompt:** сейчас prompt просит LLM «проверить archive.db size».
  LLM не имеет tool для измерения файла; надо либо дать ему tool
  (`file_stat /Users/pablito/.openclaw/krab_memory/archive.db`), либо заменить
  job на **прямой Python-хелпер**, который читает size и вызывает alert только
  при пороге. Текущий prompt — «мягкий» и полагается на агентный flow,
  что ненадёжно для health-alert'а.
- **Порог:** 500 MB/6h слишком высокий. База уже 472 MB → линейный рост 421MB/день
  = 105 MB/6h (норма), но спайк +455 MB/сутки 21→22 апреля был бы пропущен даже
  этим порогом. Рекомендация: threshold **>150 MB/6h** + абсолютный алерт при
  db_size > 1 GB.

### 4. `cost-budget-midday-check` — ❌
- **Sender:** та же проблема.
- **Prompt:** дать tool-call на `/api/costs/report?date=today` + `/api/costs/budget`.
  Сейчас LLM должен догадаться откуда брать данные.
- **Schedule:** 13:00 — ок для midday; но если бюджет превышен утром,
  предупреждение опоздает на часы. Рассмотреть дополнительный check в 10:00.

## Action items (для отдельной сессии — не фиксить в этом аудите)

1. **P0 —** добавить `cron_native_scheduler.bind_sender(self._run_cron_prompt)` в
   `userbot_bridge.py` рядом с `krab_scheduler.bind_sender` (line ~1780).
   Метод `_run_cron_prompt(chat_id, prompt)` должен: вызвать LLM pipeline,
   если ответ ≠ `NO_REPLY` → отправить в Saved Messages.
2. **P1 —** добавить guard: если `_sender is None` при `_run_job` — логировать
   `cron_native_job_dropped_no_sender` (error, не silent-success) и **не** вызывать
   `mark_run()` (иначе `run_count` врёт).
3. **P2 —** разобраться со scheduler restart storm — похоже hot_reload триггерит
   рестарт userbot в цикле; найти source.
4. **P2 —** `archive-growth-alert-6h`: заменить LLM-job на детерминированный
   Python-хелпер (file stat + threshold).
5. **P3 —** добавить unit-тест, который ловит `bind_sender` регрессию — в
   `tests/unit/test_cron_native_scheduler.py` проверить, что на старте userbot
   sender привязан.

## Refs

- Scheduler: `src/core/cron_native_scheduler.py:96-119` (`_run_job`, guard на `self._sender`)
- Store: `src/core/cron_native_store.py` (`mark_run`, `next_due`)
- Bind site (missing): `src/userbot_bridge.py:1815` (где `start()` вызван, но `bind_sender` рядом нет)
- Рабочий аналог: `krab_scheduler.bind_sender` @ `src/userbot_bridge.py:1780`
