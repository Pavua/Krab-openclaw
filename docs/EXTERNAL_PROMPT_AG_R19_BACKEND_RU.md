# R19 BACKEND STREAM (Antigravity)

Работаешь в репозитории:
`/Users/pablito/Antigravity_AGENTS/Краб`

## Цель

Закрыть backend-риски по стабильности ответов и каналов:
1. исключить silent-failure;
2. исключить утечку служебного/tool текста в user-facing ответы;
3. стабилизировать fallback при проблемах локальной модели LM Studio.

## Жёсткие ограничения

1. Не ломай существующие команды и API контракты.
2. Не меняй секреты/ключи/`.env`.
3. Все пользовательские fallback-сообщения — короткие и без тех-мусора.
4. Не убирать существующие guard-механизмы, только усиливать.

## Основные файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/src/handlers/ai.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/openclaw_client.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/model_manager.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/task_queue.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/` (новые/обновлённые тесты)

## Пакет задач (большой, одним заходом)

### 1) Reply Completion Guarantee

Проверь все ветки, где сообщение может остаться в подвешенном состоянии (`думaю`, реакция есть, а ответа нет).

Сделай гарант финала:
- либо нормальный ответ,
- либо короткий fallback вида:
  - "Секунду, локальная модель перезапускается. Повтори через 5-10 секунд.",
- либо явный отказ с кодом причины в логах.

Добавь/усиль метрику:
- `reply_incomplete_guard_triggered`.

### 2) Runtime error sanitizer hardening

Усиль фильтрацию для user-facing контента:
- `<|begin_of_box|>`, `<|end_of_box|>`,
- `The user is asking ...`, `I will now call ...`,
- `The model has crashed ...`, `400 No models loaded ...`,
- raw action/json scaffold.

Требование:
- в обычных каналах пользователь никогда не видит этот сырой текст.

### 3) LM Studio fallback policy

Стабилизируй поведение при проблемах локальной модели:
- transient ошибки (`model crashed`, `no models loaded`, timeout) -> retry/fallback по политике;
- fatal auth/config ошибки -> не ретраить бесконечно;
- после восстановления локальной модели — корректный возврат к primary маршруту.

### 4) Queue SLA и user notification

Укрепи SLA очереди:
- выявляй долгие задачи,
- не оставляй сообщения в вечном ожидании,
- отправляй корректное уведомление пользователю при abort/timeout.

### 5) Диагностика для оператора

Сделай структурированную диагностику (без изменения публичных контрактов, если не нужно):
- стабильные `error_code`,
- человекочитаемый `summary`,
- признак `retryable`.

## Acceptance Criteria

1. Нет silent-failure: каждый входящий запрос заканчивается предсказуемо.
2. Нет tool/scaffold утечек в пользовательский канал.
3. При падении LM Studio пользователь получает нормальный fallback, а не raw runtime dump.
4. Очередь не зависает в вечном "позиция в очереди".

## Проверка (обязательно)

1. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_openclaw_client_health.py`
2. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_model_router_stream_fallback.py`
3. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_r16_queue_sla_abort.py /Users/pablito/Antigravity_AGENTS/Краб/tests/test_queue_timeout_user_notification.py`
4. Добавленные/обновлённые новые тесты по своим изменениям.

## Формат сдачи

1. `git diff --name-only`
2. Что именно исправлено
3. Какие тесты запущены и результаты
4. Какие edge-case риски остались
