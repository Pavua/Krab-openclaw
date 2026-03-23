# Audit Status 2026-03-23 RU

Этот файл фиксирует компактный truthful-статус по `IMPROVEMENTS.md`, handoff и
последним проверкам в multi-account контуре. Он не заменяет
`SESSION_HANDOFF.md`, а даёт быстрый operational summary для следующего doc update
и следующего чата.

## Confirmed

- `#4 OOM / Whisper`: подтверждено по коду в
  `Krab Voice Gateway/app/stt_engines.py` (`_whisper_lock = asyncio.Lock()`,
  сериализация `orchestrate_stt(...)`).
- `#5 Gateway self-healing`: подтверждено по коду и smoke.
  LaunchAgent-aware start есть, watchdog-фиксы есть, USER3 launcher дополнительно
  получил truthful readiness-loop и per-account logs.
- `#9 Vision / photo route`: подтверждено по коду и тестам.
  Фото обрабатываются нативно через `src/userbot_bridge.py`, regression:
  `tests/unit/test_userbot_photo_flow.py`.
- `#14 OpenClaw update`: подтверждено operationally.
  После обновления OpenClaw gateway и owner/web-панель поднимаются, Control UI
  жив, recent smoke проходит.
- `#15 Burst coalescing`: подтверждено по коду.
  В `src/userbot_bridge.py` есть `_coalesce_private_text_burst(...)`,
  `private_text_burst_coalesced`, `skip_batched_followup_message`.
- `handle_shop` startup crash: подтверждено.
  `src.handlers` снова экспортирует `handle_shop`, regression:
  `tests/unit/test_handlers_exports.py`.

## Partial

- `#6 Telegram timeouts`: частично.
  В handoff/Claude-context этот пункт отмечен как улучшенный на стороне runtime,
  но в текущем `USER3 ~/.openclaw/openclaw.json` `channels.telegram.timeoutSeconds`
  и `retry` не подтверждаются. Глобально закрытым считать нельзя.
- `#7 Long-request transparency`: частично.
  Постоянный `typing` подтверждён (`_keep_typing_alive(...)`), но промежуточные
  owner-visible tool-status сообщения уровня `Вызываю инструмент...` /
  `Читаю скриншот...` пока не подтверждены как законченный UX-слой.
- `#10 Mercadona`: частично.
  Команда `!shop` и `src/skills/mercadona.py` есть, антибот/XHR логика добавлена,
  но acceptance и отдельного тестового покрытия для самого поиска пока нет.
- `Voice Gateway` на другой учётке: частично.
  Штатный launcher упирается в права на `pablito/shared` path, но per-account
  fallback уже реально поднимает gateway из
  `~/.openclaw/krab_runtime_state/voice_gateway`.

## Still Open

- `#1 Swarm / product teams`
- `#2 macOS Permission Audit`
- `#3 HomePod integration`
- `#8 Telegram transport voice/document`
- `#11 Inbox folder`
- `#12 global macOS hotkey`
- `#13 Hammerspoon window control`

## Fresh Verification

- `./venv/bin/python -m pytest tests/unit/test_handlers_exports.py tests/unit/test_telegram_session_watchdog.py -q`
  → `7 passed`
- `./venv/bin/python -m pytest tests/unit/test_userbot_photo_flow.py tests/unit/test_userbot_stream_timeouts.py -q`
  → `14 passed`
- прямой импорт `from src.userbot_bridge import KraabUserbot`
  ранее проходил в USER3-контуре;
- recent live smoke поднимал owner panel `:8080`, OpenClaw gateway `:18789` и
  per-account Voice Gateway fallback `:8090`.

## Для следующего doc pass

- обновлять `SESSION_HANDOFF.md` addendum'ом, а не переписывать старую историю;
- обновлять `QUICK_START_NEXT_SESSION.md`, чтобы следующий чат видел:
  - `handle_shop` fix;
  - truthful confirmed/partial matrix;
  - multi-account note про per-account runtime state и Voice Gateway fallback;
- не менять baseline в `MASTER_PLAN_SOURCE_OF_TRUTH.md`, если не пересматривается
  сам master-plan.
