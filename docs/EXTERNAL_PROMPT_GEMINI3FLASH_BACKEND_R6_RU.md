# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R6)

Работаем в backend ownership Antigravity.
Модель: Gemini 3.1 Flash.

## Цель
Дожать операторскую надежность `telegram_control + group moderation`:
1) единый формат ошибок/next-step в Telegram Control,
2) предсказуемый explain-output для AutoMod решений,
3) тесты регрессии на edge-кейсы.

## Разрешенные файлы
- `src/handlers/telegram_control.py`
- `src/core/telegram_chat_resolver.py`
- `src/core/telegram_summary_service.py`
- `src/core/group_moderation_engine.py`
- `src/handlers/groups.py`
- `tests/test_telegram_control.py`
- `tests/test_telegram_chat_resolver.py`
- `tests/test_telegram_summary_service.py`
- `tests/test_group_moderation_engine.py`

## Что сделать
1. Telegram Control hardening:
- унифицировать ошибки `summaryx`/resolver по схеме:
  - `код ошибки`,
  - `короткое объяснение`,
  - `что сделать дальше`;
- убрать дубли шаблонов через локальные helper-функции в пределах файла.

2. Group moderation explain v2:
- в `group_moderation_engine` добавить компактный explain-пакет в решение:
  - `primary_rule`,
  - `matched_rules[]`,
  - `action_source` (rule/policy/template),
  - `dry_run_reason` (если dry-run);
- в `groups.py` использовать explain-пакет в DRY-RUN уведомлении (короче, без шума).

3. Тесты:
- покрыть 3 сценария:
  - summaryx: пустая история + битый target,
  - automod: несколько нарушений одновременно (детерминированный primary_rule),
  - automod dry-run: есть `dry_run_reason` и корректный explain.

## Ограничения
1. Не менять файлы из codex ownership.
2. Не трогать web frontend (`src/web/*`).
3. Не вносить новые зависимости.

## Обязательные проверки
- `python3 scripts/check_workstream_overlap.py`
- `pytest -q tests/test_telegram_control.py tests/test_telegram_chat_resolver.py tests/test_telegram_summary_service.py tests/test_group_moderation_engine.py`

## Формат сдачи
1. Изменённые файлы
2. Что реализовано
3. Команды тестов
4. Результаты тестов
5. Риски/ограничения
