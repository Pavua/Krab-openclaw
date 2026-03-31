# Telegram Runtime Recovery And MCP Status

Дата: 2026-03-31
Репозиторий: `/Users/pablito/Antigravity_AGENTS/Краб`

## Что было проверено

- Живой runtime Краба на `:8080`
- Живой OpenClaw gateway на `:18789`
- Внутренний Telegram userbot lifecycle после ночной деградации
- Наличие repo-level skills у учётки `pablito`
- Фактический статус двух Telegram MCP контуров

## Что оказалось корнем ночного зависания

Проблема не в OpenClaw scope-header и не в поломке облачного ключа.
Текущий ночной инцидент оказался связан с lifecycle-гонкой Telegram transport:

- heartbeat watchdog делал `get_me()` параллельно с `stop()/start()`;
- фоновые задачи отменялись без `await`, из-за чего старый probe мог продолжать работу по уже закрываемому сокету;
- web restart path вызывал голый `stop()+start()` без общего restart-lock;
- внешний watchdog долго видел `running`, даже если transport уже деградировал.

## Что было исправлено в коде

- В `src/userbot_bridge.py` добавлен сериализованный `restart()` для userbot.
- В `src/userbot_bridge.py` stop-path теперь ждёт завершения фоновых watchdog-задач после `cancel()`.
- В `src/userbot_bridge.py` добавлено честное состояние `degraded` при повторных probe failures.
- В `src/userbot_bridge.py` добавлена защита от висячих `Session.restart()` task'ов Pyrogram.
- В `src/userbot_bridge.py` добавлен shutdown-guard для `session.restart()`, чтобы внутренний Pyrogram auto-restart не лез в уже закрываемую sqlite storage во время controlled stop/restart.
- В `src/userbot_bridge.py` `closed database` теперь считается non-fatal shutdown-шумом sqlite storage.
- В `src/modules/web_app.py` endpoint `/api/krab/restart_userbot` переведён на `restart()`, если метод доступен.
- В `src/modules/web_app.py` и `scripts/telegram_session_watchdog.py` наружу выведен и учитывается `telegram_userbot_client_connected`.

## Что подтверждено проверками

- `http://127.0.0.1:8080/api/health/lite` возвращает:
  - `telegram_userbot_state: running`
  - `telegram_userbot_client_connected: true`
- `http://127.0.0.1:18789/health` возвращает `{"ok": true, "status": "live"}`
- Focused unit pack проходит:
  - `tests/unit/test_userbot_startup.py`
  - `tests/unit/test_telegram_session_watchdog.py`
  - `tests/unit/test_web_app_runtime_endpoints.py`
- После добавления shutdown-guard focused unit pack проходит как `150 passed`.
- Owner web panel открывается и показывает живой runtime.
- Live `POST /api/krab/restart_userbot` возвращает `ok: true`, а health после restart снова возвращается в `running`.

## MCP truth по аккаунтам

Фактический mapping по session SQLite-файлам:

- `krab-telegram` -> `yung_nagato` (`user_id=6435872621`)
- `krab-telegram-test` -> `p0lrd` (`user_id=312322764`)

Оба MCP сервера проходят ручной stdio handshake:

- `initialize` -> `ok`
- `tools/list` -> `11` инструментов

Список инструментов одинаковый на обоих контурах:

- `telegram_get_dialogs`
- `telegram_get_chat_history`
- `telegram_send_message`
- `telegram_download_media`
- `telegram_transcribe_voice`
- `telegram_search`
- `telegram_edit_message`
- `krab_status`
- `krab_tail_logs`
- `krab_restart_gateway`
- `krab_run_tests`

## Skills truth

Для `pablito` дотянут missing repo-level skill:

- `~/.codex/skills/krab-openclaw-workspace-prompt-curator`

Это выровняло Codex skill-layer с ожидаемым набором developer skills на основной учётке.

## Остаточный риск

После controlled restart runtime жив и отвечает. Исторические traceback'и в старом `logs/krab_launchd.err.log` остаются как legacy evidence прошлых падений, но в текущем прогоне этот файл не обновлялся. Остаточный риск всё ещё формулируется аккуратно: Pyrogram внутренне управляет restart-task'ами сам, поэтому при новых экзотических network race может понадобиться отдельный vendor-level hardening. На текущем шаге блокирующего симптома уже не наблюдается.
