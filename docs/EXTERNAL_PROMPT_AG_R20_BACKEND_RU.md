# AG Prompt R20 Backend — Deep Health Robustness

Контекст:

- Проект: `/Users/pablito/Antigravity_AGENTS/Краб`
- Текущий deep endpoint: `GET /api/health` (через `EcosystemHealthService.collect`)
- Быстрый endpoint уже есть: `GET /api/health/lite`

Задача:
Укрепить backend deep-health так, чтобы endpoint был предсказуемее под нагрузкой и не зависал из-за одного медленного источника.

Требования:

1. В `EcosystemHealthService.collect()` перевести проверки на конкурентное выполнение (`asyncio.gather`) с локальными timeout-guard.
2. Если один источник timeout/error:
   - возвращать частичный report,
   - не ронять весь endpoint,
   - явно помечать degraded статус этого источника.
3. Добавить latency/timeout диагностику в report (минимально инвазивно).
4. Не менять контракт критичных ключей, которые уже использует UI.
5. Сохранить strict HTTP-логику для Krab Ear.

Файлы (ожидаемо):

- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/ecosystem_health.py`
- (при необходимости) `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_ecosystem_health.py`

Тесты:

1. Targeted pytest только по затронутым тестам.
2. Проверка `/api/health` на живом запуске (без падений endpoint).

Формат ответа:

1. Измененные файлы.
2. Краткий diff-summary.
3. Команды тестов + фактические результаты.
4. Что осталось риском.
