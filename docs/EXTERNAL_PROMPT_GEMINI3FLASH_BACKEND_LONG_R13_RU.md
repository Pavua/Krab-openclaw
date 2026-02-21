# EXTERNAL PROMPT — GEMINI 3 FLASH (BACKEND LONG R13)

## Контекст
Длинный backend-цикл для Krab/OpenClaw с фокусом на **стабильность Telegram в cloud/force_cloud**, прозрачную диагностику и безопасный fallback. Выполняй этапы последовательно в одном диалоге и сдавай единым отчётом.

## Жёсткие границы
1. Не трогай frontend (`src/web/**`) — это отдельный поток.
2. Не меняй внешние проекты и сторонние репозитории.
3. Не трогай `.env`, ключи, токены и секцию секретов.
4. Не удаляй существующие рабочие API/команды без обратной совместимости.

## Цели длинного цикла (R13)

### Этап A — Telegram Cloud Reliability
1. Довести устойчивость cloud-ветки в `src/core/model_manager.py` и `src/handlers/ai.py`:
- при runtime-ошибках cloud не отдавать «сырой» текст как валидный ответ;
- гарантировать корректное завершение pipeline (placeholder должен завершаться финальным сообщением);
- если первый cloud-кандидат упал — обязателен переход к следующему кандидату (в рамках лимита);
- единый user-facing формат ошибок для Telegram/WA/iMessage.

2. Расширить нормализацию runtime-ошибок:
- сеть/timeout/connection/no route/provider unavailable;
- payload-ошибки OpenClaw (`NOT_FOUND`, `upstream`, `provider`, `gateway`).

3. Не допускать зависаний очереди:
- если ответ не получен, задача должна корректно закрываться;
- обязательно логировать причину завершения (успех/ошибка/timeout).

### Этап B — Observability и Diagnostics
1. Ввести детальную телеметрию маршрутизации:
- reason/detail по каждой попытке cloud;
- число попыток и финальный кандидат;
- причина последнего fail в канале.

2. Расширить health/diagnostics endpoint (без breaking change):
- добавить блок по cloud reliability (last_error, last_provider, retry_count, force_mode);
- добавить краткую сводку по каналам (telegram/whatsapp/imessage/signal) если доступно через текущие данные.

3. Гарантировать, что диагностика не падает при частично недоступных зависимостях.

### Этап C — Safe Fallback Policy
1. Формализовать поведение force_cloud:
- force_cloud = только облако, но с переключением между cloud-кандидатами;
- при полном fail — понятный user-facing ответ + корректная телеметрия причины.

2. Для auto/local-first:
- cloud fallback только при явном fail local;
- не больше одного fallback-перехода в рамках одного запроса.

3. Проверить, что нет рекурсивного «retry storm».

### Этап D — Regression Tests (обязательно)
1. Добавить/обновить тесты:
- `tests/test_model_router_stream_fallback.py`
- `tests/test_model_router_phase_d.py`
- при изменении handler-пайплайна: `tests/test_auto_reply_queue.py`, `tests/test_handlers.py`

2. Добавить минимум 3 новых кейса:
- cloud candidate #1 = `Connection error.`, candidate #2 = success;
- force_cloud all failed -> корректное финальное сообщение и телеметрия;
- placeholder lifecycle в Telegram завершается (нет вечного «Думаю...»).

## Команды проверки (обязательно прогнать)
1. `python3 scripts/check_workstream_overlap.py`
2. `pytest -q tests/test_model_router_stream_fallback.py tests/test_model_router_phase_d.py`
3. Если тронуты handlers: `pytest -q tests/test_auto_reply_queue.py tests/test_handlers.py`

## Формат финального отчёта
1. Что сделано по этапам A/B/C/D.
2. Список изменённых файлов.
3. Новые/обновлённые тесты.
4. Точные команды и результаты тестов.
5. Остаточные риски + что осталось Codex-слою.

## Важно
Если упираешься в blocker по ownership/контракту — явно зафиксируй blocker и не импровизируй с ломкой архитектуры.
