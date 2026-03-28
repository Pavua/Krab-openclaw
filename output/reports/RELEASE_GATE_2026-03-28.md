<!--
Что это:
Итоговый release-gate срез по ветке после серии runtime/UI/Telegram фиксов.

Зачем:
Дать truthful verdict по обязательным и advisory проверкам, не опираясь на память.
-->

# Release Gate — 2026-03-28

## Scope

Проверка собрана для текущего среза ветки `codex/voice-recovery-20260326` после закрытия:

- voice runtime recovery;
- Telegram owner/group hygiene и fallback surface;
- launcher/self-healing правок;
- truthful owner inbox lifecycle;
- owner panel cached first paint и cold-reload recovery.

## Что запускалось

### Обязательные / gate

- `python3 scripts/pre_release_smoke.py`
- `python3 scripts/r20_merge_gate.py`
- `GET http://127.0.0.1:8080/api/health/lite`

### Live / advisory

- `python3 scripts/live_channel_smoke.py --max-age-minutes 60`
- `GET http://127.0.0.1:8080/api/inbox/status`
- `GET http://127.0.0.1:8080/api/voice/runtime`

## Результаты

### Pre-release smoke

Артефакт: `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/pre_release_smoke_20260328_163019.json`

- `ok = true`
- `required_failed = []`
- `blocked_required = []`
- `advisory_failed = []`
- `blocked_advisory = []`
- runtime owner truth:
  - `current_user = pablito`
  - `web_owner = pablito`
  - `gateway_owner = pablito`
  - `health_ok = true`

Ключевые шаги из smoke:

- `syntax_core` -> `OK`
- `unit_runtime_core` -> `141 passed, 1 warning`
- `autoswitch_dry_run` -> `OK`
- `live_channel_smoke` -> `OK`
- `swarm_live_smoke_mock` -> `OK`
- `e1e3_acceptance` -> `OK`
- `r20_merge_gate` -> `OK`
- `channels_probe` -> `web truth: passed=6 skipped=0 failed=0`
- `signal_alert_route` -> `OK` с предупреждениями, но без blocker-а

### R20 merge gate

Артефакт: `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/r20_merge_gate_20260328_152850Z.json`

- `ok = true`
- `required_failed = 0`
- `advisory_failed = 0`

Ключевые шаги:

- `pytest_targeted_r20` -> `135 passed, 1 warning`
- `compile_web_health_modules` -> `OK`
- `http_lite_health` -> `status=200`
- `http_deep_health` -> `status=200`

### Live runtime truth

- `GET /api/health/lite` -> `200`, `telegram_userbot_state=running`, `telegram_session_state=ready`, `lmstudio_model_state=idle`
- `GET /api/inbox/status` -> `200`, `open_items=0`, `acked_items=0`, `stale_open_items=0`, `stale_processing_items=0`
- `GET /api/voice/runtime` -> `200`, `enabled=true`, `delivery=text+voice`, `live_voice_foundation=true`
- `live_channel_smoke` -> все 6 обязательных каналов `OK`, лог-хвост без новых warnings/errors

## Verdict

### Blockers

- Обязательных blocker-ов по текущему срезу не найдено.

### Residual risks

- В рабочем дереве остаётся большой объём несвязанных локальных изменений вне этого блока; для merge в `main` нужен дисциплинированный отбор только проверенного среза, а не попытка тащить весь dirty worktree целиком.
- `Browser / MCP Readiness` намеренно остаётся volatile probe и не подменяется fake cached-ready состоянием.
- `signal_alert_route` проходит без blocker-а, но всё ещё может давать предупреждения уровня environment/setup.

### Итог

Текущий проверенный срез ветки проходит release gate как `release-candidate` для своего объёма изменений. На уровне кода, runtime, owner UI и live transport обязательных незакрытых поломок сейчас не видно.
