# Antigravity Task Pack R22 (2 окна, крупные блоки)

## Цель спринта

Добить стабильность управления каналами и роутингом без дублирования ответственности:

1. Явно разделить транспорт каналов (OpenClaw) и policy/роутинг (Krab).
2. Убрать операторскую неоднозначность в UI (что реально применилось: local/cloud/auto).
3. Закрыть UX-долг по русской локализации и диагностике в Web Panel.

## Окно A — Backend (крупный блок)

Запустить prompt:

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R22_BACKEND_RU.md`

Рекомендуемая модель:

- `Claude Sonnet 4.6 (Thinking)`

Fallback-модель:

- `Gemini 3.1 Pro (High)`

## Окно B — Frontend (крупный блок)

Запустить prompt:

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R22_FRONTEND_RU.md`

Рекомендуемая модель:

- `Gemini 3.1 Pro (High)`

Fallback-модель:

- `Claude Sonnet 4.6 (Thinking)`

## Контракт между окнами (фиксируем заранее)

1. Backend добавляет read-only endpoint’ы:
   - `GET /api/openclaw/control-compat/status`
   - `GET /api/openclaw/routing/effective`
2. Frontend использует только эти endpoint’ы + уже существующие:
   - `/api/model/catalog`
   - `/api/openclaw/model-autoswitch/status`
   - `/api/openclaw/channels/status`
   - `/api/assistant/query`

## Общие ограничения

1. Ветка: `codex/queue-forward-reactions-policy`.
2. Не трогать unrelated файлы.
3. Комментарии/docstring в коде только на русском.
4. Никаких destructive git-команд.
5. В конце каждого окна дать:
   - список файлов,
   - фактические команды проверок,
   - фактический вывод тестов,
   - остаточные риски.

## Definition of Done

1. В Web Panel видно:
   - фактический routing mode,
   - совместимость с Control UI,
   - что runtime каналов живой даже при schema warning.
2. Публичные API-контракты не сломаны.
3. `pytest` по измененным зонам зеленый.
4. Browser smoke на `http://127.0.0.1:8080` пройден (скриншоты приложены).
