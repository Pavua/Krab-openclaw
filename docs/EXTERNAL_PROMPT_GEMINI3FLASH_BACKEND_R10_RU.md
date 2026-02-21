# EXTERNAL PROMPT — GEMINI 3 FLASH (BACKEND R10)

## Контекст
Нужно закрыть критичный runtime-gap: в Telegram локальная модель подхватывается корректнее, а в iMessage/WhatsApp иногда прилетает `400 No models loaded` / `model crashed`, и автоответ уходит с ошибкой вместо облачного fallback.

Цель R10: **гарантировать устойчивый fallback для всех каналов** + дать backend-контур для управляемой загрузки/выгрузки локальной модели из web API.

## Границы (не нарушать)
1. Не менять frontend (`src/web/index.html`) — это отдельный поток.
2. Не трогать внешние проекты (`Krab Ear`, `Krab Voice Gateway`).
3. Не ломать существующие endpoint и тесты.
4. Не менять codex-only зоны (соблюдать ownership).

## Файлы (целевые)
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/model_manager.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_model_router_phase_d.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`

## Что сделать
1. **Universal fallback при local runtime error**:
   - В роутере (model_manager) при локальном канале, если ответ содержит сигнатуры вроде:
     - `no models loaded`
     - `please load a model`
     - `model has crashed`
     - `connection error/refused` от локального движка
   - Выполнить немедленный retry в cloud-канал в рамках того же запроса.
   - Вернуть в telemetry явный `route_reason` типа `local_failed_cloud_fallback`.

2. **Guard от циклов retry**:
   - Не допускать бесконечной рекурсии fallback.
   - Максимум 1 fallback-переход local -> cloud на один запрос.

3. **Web API для управления локальной моделью (под WEB_API_KEY)**:
   - `GET /api/model/local/status`:
     - loaded/not_loaded
     - model name (если есть)
     - probe source
   - `POST /api/model/local/load-default`:
     - загрузить дефолтную локальную модель (из текущего runtime config/router).
   - `POST /api/model/local/unload`:
     - выгрузить локальную модель (или unload all, если точечная выгрузка недоступна).
   - Все write endpoint должны требовать `X-Krab-Web-Key`.

4. **Обработка ошибок API**:
   - Структурированный ответ (`ok`, `error`, `detail`, `exit_code` при subprocess).
   - Таймауты и безопасный запуск subprocess без shell injection.

## Тесты (обязательно)
1. `tests/test_model_router_phase_d.py`:
   - local failure -> cloud fallback (успешный retry).
   - fallback не повторяется бесконечно (1 попытка).
2. `tests/test_web_app.py`:
   - `GET /api/model/local/status` (ok).
   - write endpoint без ключа -> 403.
   - write endpoint с ключом -> 200 и корректный payload.

## Критерий готовности
1. `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py` проходит.
2. Изменения только в указанных backend-файлах.
3. В отчете перечислить:
   - какие fallback-сигнатуры добавлены;
   - как предотвращен loop;
   - примеры payload новых endpoint.
