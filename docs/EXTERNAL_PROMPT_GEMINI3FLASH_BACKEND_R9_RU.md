# EXTERNAL PROMPT — GEMINI 3 FLASH (BACKEND R9)

## Контекст
Нужно усилить backend web API, чтобы управление OpenClaw из панели было максимально полным и практичным.
Текущие новые endpoint уже есть:
- `/api/openclaw/model-autoswitch/status`
- `/api/openclaw/model-autoswitch/apply`

Твоя задача: **добавить недостающий backend-контур управления каналами OpenClaw**.

## Границы (не нарушать)
1. Не трогать фронтенд (`src/web/index.html`) — это отдельный поток.
2. Не менять внешние проекты (`/Users/pablito/Antigravity_AGENTS/Krab Ear`, `Krab Voice Gateway`).
3. Не ломать существующие endpoint и тесты.

## Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`

## Что сделать
1. Добавить read endpoint:
   - `GET /api/openclaw/channels/status`
   - внутри выполнить `openclaw channels status --probe`
   - вернуть структурированный ответ:
     - `ok`
     - `raw` (полный текст)
     - `warnings` (массив строк, если есть блок Warnings)
2. Добавить write endpoint (под `WEB_API_KEY`):
   - `POST /api/openclaw/channels/runtime-repair`
   - выполнить `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command`
   - вернуть stdout/stderr + exit_code.
3. Добавить write endpoint (под `WEB_API_KEY`):
   - `POST /api/openclaw/channels/signal-guard-run`
   - выполнить `/Users/pablito/Antigravity_AGENTS/Краб/scripts/signal_ops_guard.command --once`
   - вернуть stdout/stderr + exit_code.
4. Для всех новых subprocess-вызовов:
   - таймаут;
   - читаемые ошибки `HTTPException(500, detail=...)`;
   - без shell injection.

## Тесты (обязательно)
Добавить тесты в `tests/test_web_app.py`:
1. `GET /api/openclaw/channels/status` — успешный сценарий (mock subprocess).
2. `POST /api/openclaw/channels/runtime-repair` — требует API key.
3. `POST /api/openclaw/channels/signal-guard-run` — требует API key.
4. Успешные сценарии write endpoint при корректном `X-Krab-Web-Key`.

## Критерий готовности
1. `pytest -q tests/test_web_app.py` проходит.
2. Нет изменений вне указанных файлов.
