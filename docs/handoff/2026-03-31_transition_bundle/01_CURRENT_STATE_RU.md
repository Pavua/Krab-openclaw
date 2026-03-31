# Current State — 31.03.2026

## Главный итог

Ночной инцидент с деградацией Telegram userbot разобран и закрыт до рабочей operational-точки.
Корень был в lifecycle-гонке между watchdog probe и `stop()/start()` Pyrogram-клиента.

## Что сейчас truth

- Репозиторий: `/Users/pablito/Antigravity_AGENTS/Краб`
- Активная ветка для продолжения: `codex/telegram-runtime-recovery-handoff`
- Draft PR: `#2`
- Runtime health:
  - `http://127.0.0.1:8080/api/health/lite` -> `ok=true`
  - `http://127.0.0.1:18789/health` -> `ok=true`
- Userbot runtime state:
  - `telegram_userbot_state=running`
  - `telegram_userbot_client_connected=true`

## Что изменено

- `src/userbot_bridge.py`
  - сериализованный `restart()`
  - await на фоновых watchdog task после `cancel()`
  - truthful `degraded` state
  - защита от поздних `Session.restart()` задач Pyrogram
  - shutdown-guard на `session.restart()`
- `src/modules/web_app.py`
  - `/api/krab/restart_userbot` использует `restart()`
  - `/api/health/lite` возвращает `telegram_userbot_client_connected`
- `scripts/telegram_session_watchdog.py`
  - учитывает `telegram_userbot_client_connected`
- unit tests
  - покрывают restart/recovery/shutdown guard

## MCP truth

Подтверждено по `.session` SQLite-файлам:

- `krab-telegram` -> `@yung_nagato`
- `krab-telegram-test` -> `@p0lrd`

Оба MCP сервера вручную проходили:

- `initialize`
- `tools/list`

и публиковали по 11 tools.

## Что важно понимать про текущий Codex-чат

Текущий чат может не видеть Telegram MCP как callable tools, даже при корректном `~/.codex/config.toml`.
Это не означает, что конфиг или session-файлы сломаны.
Практический fixed path:

- открыть новый чат или перезапустить Codex;
- после этого проверить, что registry перечитан.

## Остаточный риск

На текущем шаге блокирующий симптом не воспроизводится, но vendor-level риск внутри Pyrogram честно считаем не нулевым:

- библиотека сама создаёт internal restart-task'и;
- при экзотических network race теоретически может понадобиться ещё более глубокий hardening на vendor-уровне.

Сейчас это не блокер merge-review и не блокер продолжения работ.
