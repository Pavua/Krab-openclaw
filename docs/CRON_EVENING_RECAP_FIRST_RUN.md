# Cron evening-recap — first real fire verification

**Дата проверки:** 2026-04-25 01:34 CEST (постфактум, через ~3.5h после fire)
**Branch:** `fix/daily-review-20260421`
**Bind commit:** `2e9e504 fix(cron): bind LLM-processing sender to cron_native_scheduler (was silent no-op!)`
**Watch commit (this report):** `docs(cron): evening-recap first real fire verification`

## Verdict — **BUG, требуется fix в Wave 12**

| Check | Result |
|-------|--------|
| 22:00 fire detected (`cron_native_job_firing`) | YES |
| Saved Messages получили evening recap | NO |
| `cron_job_message_sent` event в логах | NO |
| `cron_job_silent_skip` event в логах | NO |
| `cron_job_llm_failed` event в логах | NO |
| `cron_job_skip_no_telegram` event | NO |
| `last_run_at` обновился в `cron_native_jobs.json` | YES (`2026-04-24T22:00:29.367950+00:00`) |
| `run_count` инкрементирован (1) | YES |

## Что произошло (timeline)

```
2026-04-25 00:00:29  swarm_scheduler_stopped
2026-04-25 00:00:29  cron_native_scheduler_stopped       ← scheduler уже остановлен
2026-04-25 00:00:29  cron_native_job_firing  daily-evening-recap  '0 22 * * *'
2026-04-25 00:00:29  cron_native_job_done    daily-evening-recap  ← в ту же секунду
2026-04-25 00:00:29  swarm_stale_lock_cleaned
2026-04-25 00:00:29  swarm_team_client_start_failed  OperationalError('disk I/O error')
```

Cron fired в момент **рестарт-цикла Krab**. По логам видно чудовищный restart loop:
175 × `cron_native_scheduler_started` против 142 × `cron_native_scheduler_stopped` —
userbot перезапускается каждые ~30 секунд весь день. Каждый рестарт приводит
к "Krab System Online" пингу в Saved Messages (id 14213…14233 за последние 35 минут).

## Root cause analysis (гипотеза для Wave 12)

`cron_native_job_done` возникает только после `await self._sender(...)` в
`src/core/cron_native_scheduler.py:108-111`. Тот факт, что `cron_native_job_done`
напечатан **в ту же секунду** что и `firing`, при отсутствии хотя бы ОДНОГО
из ожидаемых side-events (`cron_job_message_sent` / `cron_job_silent_skip` /
`cron_job_llm_failed` / `cron_job_skip_no_telegram` / `cron_job_skip_no_target`
/ `cron_job_empty_llm_reply`) — значит, либо:

1. **Sender не был bound** на момент fire (`if self._sender:` false-fall-through,
   `cron_native_job_done` всё равно логируется без выполнения тела) — наиболее
   вероятно: `bind_sender(self._run_cron_prompt_and_send)` вызывается на старте
   userbot_bridge, но при рестарт-цикле scheduler стартует раньше bind.

2. `asyncio.ensure_future(self._run_job(job))` создал task, и он был cancelled
   во время `cron_native_scheduler_stopped`, не успев выполнить sender — но
   тогда `cron_native_job_done` не должен был логироваться (он находится
   после await).

**Гипотеза №1 наиболее вероятна:** в `cron_native_scheduler._run_job()` ветка
`if self._sender:` тихо пропускается, и сразу логируется `done`. Отсюда:
0 events × cron_job_* при 4 успешных fires (run_count=1 для каждого).

## Содержимое сообщения

Не применимо — message не был отправлен.

## LLM latency

Не применимо — LLM не вызывался (callback не был выполнен).

## Issues found

1. **CRITICAL:** Cron jobs continue быть silent no-op даже после `2e9e504`.
   Все 4 jobs (morning-brief 08:00, midday-cost 13:00, archive-growth 18:00,
   evening-recap 22:00) сегодня инкрементировали `run_count`, но **ни одного
   message в Saved Messages не пришло**.
2. **CRITICAL:** Krab в restart loop (~30s cycle, 175 starts vs 142 stops в log).
   Корневая причина рестартов — отдельная задача, но она объясняет почему
   sender bind может не успевать.
3. **OBSERVABILITY GAP:** `cron_native_scheduler._run_job()` логирует `done`
   независимо от того, был ли sender вызван — нужно либо warning при
   `self._sender is None`, либо condition на лог.

## Recommended Wave 12 actions

- В `src/core/cron_native_scheduler.py:_run_job()` добавить
  `logger.warning("cron_native_no_sender_bound", job_id=job_id)` в else-branch
  `if self._sender:`.
- Проверить порядок инициализации в `userbot_bridge.py:1888`: bind_sender
  должен происходить **до** `cron_native_scheduler.start()`.
- Разобраться с restart loop: 175 starts/142 stops за день — отдельный bug.
- После fix вручную дёрнуть один job: `curl -X POST :8080/api/openclaw/cron/jobs/run` (если есть) либо temp `cron_spec` на ближайшую минуту.
