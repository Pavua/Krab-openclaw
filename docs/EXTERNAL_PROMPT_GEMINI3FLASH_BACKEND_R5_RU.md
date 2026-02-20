# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R5)

Работаем в backend ownership Antigravity.
Модель: Gemini 3.1 Flash.

## Цель

Закрыть операционные зазоры в `provisioning + voice tools` без выхода за ownership:

1. Добавить этап валидации перед apply для provisioning,
2. Унифицировать коды/подсказки ошибок Voice Gateway в `tools` handler,
3. Усилить edge-тесты по новым сценариям.

## Разрешенные файлы

- `src/core/provisioning_service.py`
- `src/handlers/provisioning.py`
- `src/handlers/tools.py`
- `src/core/voice_gateway_client.py`
- `tests/test_provisioning_service.py`
- `tests/test_tools_voice_gateway_errors.py`
- `tests/test_voice_gateway_client.py`

## Что сделать

1. Provisioning validation:
   - добавить в сервис явный метод валидации draft перед apply (например, `validate_draft`);
   - проверять минимум:
     - валидность `entity_type`,
     - корректность `name/role`,
     - что `role` присутствует в role_templates соответствующего каталога,
     - что `settings` — словарь,
     - конфликт/обновление существующей записи по `name`.
   - формат ответа: `ok`, `errors[]`, `warnings[]`, `next_step`.

2. Команда `!provision validate <draft_id>`:
   - добавить subcommand в `src/handlers/provisioning.py`;
   - выдавать компактный операторский отчёт (PASS/FAIL + список ошибок/предупреждений + что делать дальше).

3. Voice error UX v2:
   - в `src/handlers/tools.py` унифицировать ответы об ошибках Voice Gateway с коротким кодом (`VGW_*`) и actionable подсказкой;
   - покрыть кейсы: `http_4xx`, `http_5xx`, timeout/connection, unknown exception;
   - не ломать текущие команды (`!callstart`, `!callstatus`, `!callwhy`, `!callphrase`, `!calltune`).

4. Тесты:
   - добавить/обновить тесты на:
     - provisioning validate PASS/FAIL,
     - конфликт имени в draft,
     - voice error mapping -> ожидаемый `VGW_*` код и подсказка.

## Обязательные проверки

- `python3 scripts/check_workstream_overlap.py`
- `pytest -q tests/test_provisioning_service.py tests/test_tools_voice_gateway_errors.py tests/test_voice_gateway_client.py`

## Формат сдачи

1. Изменённые файлы
2. Что реализовано
3. Команды тестов
4. Результаты тестов
5. Риски/ограничения
