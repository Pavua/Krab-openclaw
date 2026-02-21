# Parallel Status Board (Krab)

## Актуально на 2026-02-20

## Потоки и ответственность

1. **Codex (этот диалог)**  
   - Зона: runtime/Signal/интеграционный гейт/операционная документация.
   - Статус: `IN_PROGRESS (R5)`.

2. **Antigravity (диалог backend/telegram)**  
   - Зона: Telegram/handler улучшения в ownership-путях Antigravity.
   - Статус: `READY_FOR_R5_PROMPT`.

3. **Gemini 3.1 Pro + Nano Banana Pro (диалог frontend/design)**  
   - Зона: дизайн/прототипы UI в frontend ownership-путях.
   - Статус: `READY_FOR_R5_PROMPT`.

## Последние обновления Codex потока

1. Принята поставка backend:
   - `./scripts/accept_backend_delivery.command`
   - результат: `26 passed`, overlap = `0`.
2. Принята и промоутнута поставка frontend в production:
   - `./scripts/accept_and_promote_frontend.command --promote`
   - runtime parity + compatibility = green.
3. Поднят и проверен Signal Ops Guard:
   - `scripts/signal_ops_guard.py`
   - `scripts/signal_ops_guard.command`
   - `scripts/signal_ops_guard_daemon.command`
4. Добавлен контур маршрутизации алертов:
   - `scripts/configure_alert_route.command`
   - `scripts/signal_alert_test.command`
   - `scripts/resolve_telegram_alert_target.command`

## Критичные замечания по среде

1. На хосте наблюдался memory spike процесса `pyrefly` (Antigravity extension), это не ядро Krab/OpenClaw.
2. Текущее узкое место алертов:
   - Telegram route через username даёт `chat not found`, пока бот не получил `/start` и не зафиксирован `chat_id`.
   - после `/start`: `./scripts/resolve_telegram_alert_target.command`.

## Что делает Codex сейчас (R5)

1. Доводит автоалерты до рабочего доставки в Telegram chat_id.
2. Держит Signal runtime стабильным (`works`) и контролирует инциденты через guard.
3. Готовит и координирует R5 задания для backend/frontend потоков без overlap.
4. Принимает и интегрирует только green-поставки.

## Следующий цикл после R5

1. Приёмка:
   - `./review_external_agent_delivery.command`
   - `./review_external_agent_delivery.command --full`
2. Интеграция только green-поставок (без ручного merge шумных diff).
3. Регрессионная проверка целевых тестов.
4. Обновление HANDOVER/ROADMAP по факту интеграции.
