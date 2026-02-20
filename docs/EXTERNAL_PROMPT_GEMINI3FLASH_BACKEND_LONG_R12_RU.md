# EXTERNAL PROMPT — GEMINI 3 FLASH (BACKEND LONG R12)

## Контекст
Это длинный backend-цикл (одним запуском) для Krab/OpenClaw. Делай этапы последовательно в рамках одного диалога и сдавай единым отчётом с чек-листом по этапам.

## Жёсткие границы
1. Не трогай frontend файлы (`src/web/**`) — это отдельный поток.
2. Не меняй внешние проекты (Krab Ear, Krab Voice Gateway).
3. Не трогай `.env`/секреты.
4. Не лезь в codex-only ownership зоны, если они помечены как чужие.

## Цели длинного цикла (R12)

### Этап A — Router Resilience
1. Довести fallback-устойчивость в `src/core/model_manager.py`:
- единый детектор runtime ошибок local/cloud;
- local->cloud fallback не более 1 раза на запрос;
- route telemetry: явные `route_reason` и `route_detail`.

2. Добавить явные guardrails:
- защита от пустых/мусорных ответов после fallback;
- единообразный формат ошибок для каналов.

3. Тесты:
- `tests/test_model_router_phase_d.py`
- добавь отдельные кейсы для loop-protection и degraded-output.

### Этап B — Web API Operations Layer
1. В `src/modules/web_app.py` расширить operability API:
- `GET /api/system/diagnostics` (ресурсы/модели/route-stats);
- `GET /api/model/local/status` (детализировано: loaded, engine, model_name, source);
- `POST /api/model/local/load-default`;
- `POST /api/model/local/unload`;
- `POST /api/openclaw/channels/runtime-repair` и `POST /api/openclaw/channels/signal-guard-run` довести до стабильных payload.

2. Для write endpoint обязательно:
- `WEB_API_KEY` check;
- timeout;
- безопасный subprocess (без shell injection);
- структурированные ошибки (`ok/error/detail/exit_code`).

3. Тесты:
- `tests/test_web_app.py`
- `tests/test_web_app_r10.py`
- добавить недостающие негативные кейсы (403/500/timeout).

### Этап C — Watchdog Soft Healing
1. В `src/core/watchdog.py`:
- cooldown already exists — не ломать;
- добавить soft-healing ветку по RAM threshold (если router связан);
- отправка уведомления только при успешном soft-heal.

2. В `src/main.py`:
- гарантировать wiring `watchdog.router = router`.

3. Тесты:
- `tests/test_watchdog.py`
- `tests/test_r11_diagnostics.py`.

### Этап D — Health/Budget Integration
1. В `src/core/ecosystem_health.py`:
- добавить блок `resources` (cpu/ram/load) и `budget` (если есть cost_engine), без падений при отсутствии зависимостей.

2. Убедиться, что при отсутствии `psutil` есть безопасный fallback (не краш).

3. Тесты:
- добавить/обновить unit-tests на health report shape.

## Команды проверки (обязательно прогнать)
1. `python3 scripts/check_workstream_overlap.py`
2. `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py tests/test_web_app_r10.py tests/test_watchdog.py tests/test_r11_diagnostics.py`
3. Если затронут health-слой — добавить его тест-файл в команду.

## Формат финального отчёта
1. Этап A/B/C/D: что сделано.
2. Список изменённых файлов.
3. Список новых/обновлённых тестов.
4. Точные команды и результаты тестов.
5. Риски/ограничения и что осталось.

## Важно
Если где-то blocker по ownership — явно укажи blocker и остановись на этом шаге, не нарушая границы.
