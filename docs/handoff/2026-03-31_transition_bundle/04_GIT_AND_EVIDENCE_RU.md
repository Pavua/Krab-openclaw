# Git And Evidence

## Git truth

- Ветка: `codex/telegram-runtime-recovery-handoff`
- Commit: `2bb77e4`
- PR: [#2 Harden Telegram userbot restart recovery](https://github.com/Pavua/Krab-openclaw/pull/2)

## Что вошло в recovery-блок

- `src/userbot_bridge.py`
- `src/modules/web_app.py`
- `scripts/telegram_session_watchdog.py`
- `tests/unit/test_userbot_startup.py`
- `tests/unit/test_telegram_session_watchdog.py`
- `tests/unit/test_web_app_runtime_endpoints.py`
- `docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md`
- `docs/handoff/2026-03-31_next_chat_or_user3_bootstrap_ru.md`

## Какие проверки уже были сделаны

```bash
pytest tests/unit/test_userbot_startup.py tests/unit/test_telegram_session_watchdog.py tests/unit/test_web_app_runtime_endpoints.py -q
```

Результат:

- `150 passed`

Также были сделаны живые проверки:

- `POST http://127.0.0.1:8080/api/krab/restart_userbot`
- `GET http://127.0.0.1:8080/api/health/lite`
- `GET http://127.0.0.1:18789/health`

## Где лежит подробный runtime handoff

- `docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md`

## Где лежит краткий bootstrap

- `docs/handoff/2026-03-31_next_chat_or_user3_bootstrap_ru.md`

## Важная оговорка по worktree

Текущий worktree очень грязный по unrelated изменениям.
В recovery commit и PR был вынесен только узкий блок по Telegram runtime recovery и handoff-документации.
Следующий агент не должен трактовать остальной грязный worktree как часть этого recovery-изменения.
