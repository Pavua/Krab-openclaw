# ANTIGRAVITY TASK PACK R18 (RU)

Дата: 2026-02-23  
Цель: разгрузить основной Codex-поток (debug/integration) и ускорить стабилизацию за счёт параллельной разработки.

---

## Общие правила для всех потоков

1. Работать в отдельных ветках, без затрагивания unrelated файлов.
2. Комментарии и docstring в коде только на русском.
3. На каждый поток:
   - список изменённых файлов,
   - краткий отчёт,
   - команды проверки и их вывод.
4. Не менять контракты API без явного описания migration notes.
5. Не трогать секреты/ключи в `.env`, `~/.openclaw/*`.

---

## Поток A (Frontend/UI) — Web Panel Stability UX

### Контекст
Сейчас статус ядра/health может визуально "флапать" при коротких таймаутах или переходных состояниях.

### Задача
Улучшить UX-индикацию в веб-панели так, чтобы пользователь видел:
1. `UP` / `DEGRADED` / `DOWN` вместо бинарного мигания.
2. Время последнего успешного health-check.
3. Чёткое сообщение "процесс жив, HTTP отвечает нестабильно" (если PID есть, а health в моменте недоступен).

### Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py` (только если нужен новый read-only endpoint состояния)

### Acceptance Criteria
1. Нет визуального "ложного красного" при коротких сетевых провалах.
2. Состояние обновляется без блокировки UI.
3. Есть screenshot до/после в `output/playwright/`.

### Проверка
1. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`
2. Playwright smoke: открыть панель, зафиксировать статус-переходы, приложить screenshot.

---

## Поток B (Backend) — OpenClaw API Discovery Fallback

### Контекст
На `127.0.0.1:18789` часто приходит HTML control UI вместо JSON API. Нужен безопасный fallback-путь, чтобы не ломать cloud-контур.

### Задача
Реализовать в `OpenClawClient` мягкий fallback для `chat_completions`/`get_models`:
1. Если обнаружен HTML payload или HTTP timeout от API endpoint:
   - вернуть структурированную диагностику (`error_code`, `summary`, `retryable`) без сырых HTML кусков;
   - не выбрасывать исключения наружу.
2. Для `chat_completions` добавить опциональный fallback-канал (через существующий model router fallback), не ломая текущий контракт метода.
3. Добавить unit-тесты на timeout + HTML сценарии.

### Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/openclaw_client.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_openclaw_client_health.py`
- при необходимости: `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_model_router_stream_fallback.py`

### Acceptance Criteria
1. Нет сырых HTML/traceback в пользовательском ответе.
2. Стабильный `error_code` в диагностике.
3. Все новые тесты зелёные.

### Проверка
1. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_openclaw_client_health.py`
2. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_model_router_stream_fallback.py`

---

## Поток C (Ops/QA) — Live Channel Smoke Harness

### Контекст
Нужен воспроизводимый smoke-ритуал для Telegram bot/userbot и iMessage без ручного "угадывания", где сломалось.

### Задача
Собрать mini-harness для live smoke:
1. Скрипт/command, который:
   - проверяет `openclaw channels status --probe`,
   - читает последние N строк логов,
   - ищет запрещённые паттерны:
     - `<|begin_of_box|>`, `<|end_of_box|>`,
     - `The user is asking`,
     - `I will now call the function`,
     - `The model has crashed`,
     - `400 No models loaded`.
2. Формирует короткий отчёт (`ok/failed`, найденные паттерны, файл с таймштампом).

### Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_channel_smoke.py` (новый)
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_channel_smoke.command` (новый, исполняемый)
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/ops_incident_runbook.md` (добавить usage)

### Acceptance Criteria
1. One-click smoke через `.command`.
2. Отчёт сохраняется в `artifacts/ops/`.
3. При проблеме виден конкретный паттерн и источник.

### Проверка
1. Запуск `.command` без падений.
2. Пример отчёта с `ok=true` и пример с искусственным `ok=false` (через тестовый лог-файл).

---

## Что вернуть в основной поток (формат сдачи)

1. `git diff --name-only`
2. Краткий changelog (5-10 пунктов)
3. Результаты проверок (конкретные команды + pass/fail)
4. Риски и что осталось за рамками

