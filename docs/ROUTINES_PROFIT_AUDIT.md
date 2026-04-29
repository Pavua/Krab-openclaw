# Routines Profit Audit — Session 19+

**Analysis:** ~50 MB krab_launchd.out.log (~49 restarts period)
**Finding:** 12 active asyncio background routines (не «cron jobs» — `cron_native_jobs.json` пуст).

## Оценка (cost=complexity+LLM-calls, value=consumer reads)

| # | Routine | Период | Cost | Value | Recommendation |
|---|---------|--------|------|-------|----------------|
| 1 | memory_indexer_worker | continuous | 6 | **9** (43k msgs, 9.1k chunks — база Memory Layer) | keep |
| 2 | proactive_watch | 900s | 5 | 8 (5 transitions/49 restarts, TG алерты) | keep |
| 3 | swarm_scheduler | per-job | 7 | 7 (2415 events — hot) | keep |
| 4 | krab_scheduler/reminders_queue | 30s | 2 | 8 (26 reminders fired) | keep |
| 5 | silence_schedule | tick | 2 | 7 (696 mute/unmute events) | keep |
| 6 | chat_ban_cache refresh | tick | 1 | 6 (2367 lookups) | keep |
| 7 | lm_studio_idle_watcher | tick | 2 | 6 (232 events, frees RAM) | keep |
| 8 | background_task_reaper | 60s | 1 | 5 (13 cancels) | keep |
| 9 | **error_digest_loop** | 6h | 4 | **2** (0 fired events!) | **downgrade to 24h** + add metric |
| 10 | **weekly_digest_loop** | weekly | 4 | **2** (0 fired; callback not wired) | **investigate** or disable |
| 11 | **nightly_summary** | 24h | 5 | **1** (0 fire, no consumers) | **disable** or merge to weekly |
| 12 | **cron_native_scheduler** | 30s | 1 | **0** (jobs empty, 1230 warn spam) | **guard** if jobs empty |

## Топ-5 профитных (leave alone)
1. memory_indexer_worker — питает archive.db (51 МБ)
2. swarm_scheduler — самый горячий
3. reminders_queue + krab_scheduler — direct user-value
4. silence_schedule — приватность owner
5. proactive_watch — baseline + транзишн-алерты

## Action items
- [x] cron_native_store_load_failed — 1230 warn/сессия, guard на пустой jobs file (quick win)
- [ ] nightly_summary — disable или merge в weekly_digest (дубликат scope)
- [ ] error_digest_loop — downgrade 6h → 24h, добавить Prometheus metric `krab_error_digest_fired_total`
- [ ] weekly_digest — дебажить почему callback не привязан (или disable)

## Дополнительные находки
- **1230 `cron_native_store_load_failed` warnings** — root cause: путь проверяется до создания файла. Файл сейчас существует (пустой), warnings должны прекратиться; но guard всё равно полезен.
- `_command_usage_save_loop` (5m) + `model_manager._maintenance_loop` — cost~1, value средний, не выделял как cron.
- `proactive_watch` имеет 3 sub-loops (`_auto_restart_checks_loop`, `_alert_checks_loop`, `_error_digest_loop`) — стоит проверить пересечение.

## Files для follow-up
- `src/userbot_bridge.py:1620-1748` — routines startup
- `src/core/proactive_watch.py:640-1121` — 3 sub-loops
- `src/core/nightly_summary.py:243` — orphan loop
- `src/core/cron_native_scheduler.py` — guard для пустого jobs file
