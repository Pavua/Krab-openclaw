# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R8)

Работаем строго в backend ownership Antigravity.
Модель: Gemini 3.1 Flash.

## Цель

Сделать Telegram-операции более устойчивыми в бою:

1. Ввести стабильные machine-readable error codes для summary/resolver.
2. Добавить rate-limit защиту от спама `!summaryx`.
3. Усилить тесты на race/edge кейсы.

## Разрешенные файлы

- `src/handlers/telegram_control.py`
- `src/core/telegram_chat_resolver.py`
- `src/core/telegram_summary_service.py`
- `tests/test_telegram_control.py`
- `tests/test_telegram_chat_resolver.py`
- `tests/test_telegram_summary_service.py`

## Что сделать

1. Unified error codes:
   - В `telegram_control.py` и `telegram_summary_service.py` убедиться, что все ключевые фейлы отдаются с кодами:
     - `CTRL_INVALID_PARAMS`,
     - `CTRL_RESOLVE_FAIL`,
     - `CTRL_ACCESS_DENIED`,
     - `CTRL_PROVIDER_ERROR`,
     - `CTRL_SYSTEM_ERROR`.
   - Добавить helper, который формирует user-text + короткий technical code без дублирования.

2. Summaryx anti-spam guard:
   - Реализовать легкий in-memory cooldown per-user для `!summaryx` в private/group контексте.
   - Поведение:
     - Если cooldown активен, вернуть понятный ответ с оставшимся временем.
     - Cooldown не должен блокировать owner/superuser override (если это уже заложено policy).
   - Никаких внешних хранилищ, только in-memory.

3. Resolver hardening:
   - Укрепить обработку нестандартных target-строк:
     - Пробелы/кавычки/`@@user`/`-100...` с мусором.
   - Вернуть детерминированные ошибки с next-step.

4. Тесты:
   - Unit-тесты для cooldown (включая bypass для owner/superuser).
   - Edge-тесты resolver для битых target.
   - Регрессия старых тестов summaryx.

## Ограничения

1. Не менять файлы codex ownership.
2. Не трогать web/ui.
3. Не добавлять сторонние зависимости.

## Обязательные проверки

- `python3 scripts/check_workstream_overlap.py`
- `pytest -q tests/test_telegram_control.py tests/test_telegram_chat_resolver.py tests/test_telegram_summary_service.py`

## Формат сдачи

1. Изменённые файлы.
2. Что реализовано.
3. Команды тестов.
4. Результаты.
5. Риски/ограничения.
