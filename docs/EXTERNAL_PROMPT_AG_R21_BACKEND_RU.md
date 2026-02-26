# AG Prompt R21 Backend — Ops Reports API + OpenClaw Health Load Shedding

Контекст:
- Проект: `/Users/pablito/Antigravity_AGENTS/Краб`
- Существуют артефакты в `artifacts/ops/*.json`
- Web Panel уже использует health endpoint'ы, но пока нет единого API чтения OPS-отчётов.
- В логах периодически видно timeout CLI fallback в `OpenClawClient.health_check`.

## Блок 1. Новый read-only API для OPS-репортов

### Требования
1. Добавить в `src/modules/web_app.py` endpoint:
   - `GET /api/ops/reports/catalog`
   Возвращает список поддерживаемых `report_id` и метаданные latest-файла:
   - `exists`, `path`, `mtime`, `size_bytes`.
2. Добавить endpoint:
   - `GET /api/ops/reports/latest/{report_id}`
   Возвращает JSON содержимое latest-файла, если есть.
3. Поддерживаемые `report_id`:
   - `r20_merge_gate` -> `artifacts/ops/r20_merge_gate_latest.json`
   - `krab_core_health_watch` -> `artifacts/ops/krab_core_health_watch_latest.json`
   - `live_channel_smoke` -> `artifacts/ops/live_channel_smoke_latest.json`
   - `lmstudio_idle_guard` -> `artifacts/ops/lmstudio_idle_guard_latest.json`
   - `pre_release_smoke` -> `artifacts/ops/pre_release_smoke_latest.json`
4. Если файла нет:
   - не падать, вернуть `ok=true`, `exists=false`, `payload=null`.
5. Endpoint'ы только read-only, без write-auth.

## Блок 2. OpenClaw health load shedding

### Требования
1. В `src/core/openclaw_client.py` добавить защиту от частых дорогих CLI fallback после timeout:
   - cooldown после неуспешного CLI probe (например, 20–30с),
   - в cooldown не запускать subprocess, возвращать последний известный результат/False.
2. Вынести timeout/cooldown в env-параметры (с дефолтами):
   - `OPENCLAW_HEALTH_CLI_TIMEOUT_SEC`
   - `OPENCLAW_HEALTH_CLI_COOLDOWN_SEC`
3. Не ломать текущий публичный контракт `health_check()`.

## Блок 3. Тесты

### Требования
1. Добавить/обновить tests:
   - `tests/test_web_app.py` для новых `/api/ops/reports/*` endpoint'ов,
   - `tests/test_openclaw_client_health.py` для cooldown/timeout поведения.
2. Прогнать targeted pytest и приложить фактический вывод.

## Ограничения
1. Не менять существующие контракты `GET /api/health` и `GET /api/health/lite`.
2. Не трогать unrelated модули.

## Формат ответа
1. Измененные файлы.
2. Краткий diff-summary.
3. Команды тестов + фактический вывод.
4. Что осталось риском.
