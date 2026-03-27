# Private Burst Batching Smoke — 2026-03-27

## Сценарий

- Chat: `312322764`
- Цель: подтвердить, что несколько быстрых private text-сообщений схлопываются в один batch-query, а не порождают несколько независимых AI-маршрутов.

## Что проверялось

### 1. Базовая живая проблема до фикса

- Fast self-burst marker: `BURSTFAST-20260327-171234`
- Сообщения: `11414`, `11415`, `11416`
- Наблюдение:
  - live path до фикса не дал batch-anchor;
  - появились отдельные voice-ответы `11417` и `11418`;
  - это соответствовало регрессии live self-burst и стало основанием для доработки batching-логики.

### 2. Что исправлено в коде

- В [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) `_coalesce_private_text_burst()` больше не делает единственный снимок history сразу после batch-window.
- Добавлен короткий `settle-poll`, который перечитывает history несколько раз, пока follower-сообщения не появятся или пока снимок не стабилизируется.
- Добавлен unit-regression в [tests/unit/test_userbot_message_batching.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_message_batching.py) на delayed history visibility.

## Unit verification

- Команда:
  - `source venv/bin/activate && pytest -q tests/unit/test_userbot_message_batching.py -q`
- Результат:
  - `3 passed`

## Live verification после фикса

- Marker: `BURSTMCP-20260327-172132`
- Быстрые сообщения:
  - `11423` = `part 1/3`
  - `11424` = `part 2/3`
  - `11425` = `part 3/3`

### Ключевое evidence

- Message `11425` был отредактирован userbot'ом в единый anchor и уже содержит склеенный запрос:
  - `BURSTMCP-20260327-172132 part 1/3`
  - `BURSTMCP-20260327-172132 part 2/3`
  - `BURSTMCP-20260327-172132 part 3/3 :: ...`
- При этом `11423` и `11424` остались исходными сообщениями и не превратились в отдельные AI-anchor replies.

## Verdict

- Private burst batching: `ПОДТВЕРЖДЁН`
- Что именно подтверждено:
  - transport ingestion схлопывает быстрый private burst в единый anchor;
  - follower-сообщения не стартуют отдельные независимые маршруты на этапе приёма;
  - delayed history visibility теперь покрыта unit-regression.

## Ограничение

- Для owner self-burst через отдельный Pyrogram live-harness после restart наблюдалась нестабильность доставки updates в сам userbot-контур. Для боевой верификации использован более надёжный MCP-trigger burst без конкуренции за runtime-сессию `kraab`.
