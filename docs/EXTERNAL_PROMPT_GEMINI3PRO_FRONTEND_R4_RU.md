# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R4)

Работаем строго в frontend/design контуре.
Модель: Gemini 3.1 Pro (High).

## Цель

Сделать production-ready UX полировку после промоушена:

1. улучшить mobile UX для index_redesign (<400px),
2. убрать потенциальные переполнения длинных строк/чипов,
3. сформировать короткий визуальный smoke-checklist.

## Разрешенные файлы

- src/web/index.html
- src/web/prototypes/nano/nano_theme.css
- docs/frontend_design/FRONTEND_R3_PACKAGING_NOTE_RU.md
- docs/frontend_design/FRONTEND_R4_MOBILE_QA_RU.md (новый)

## Что сделать

1. Улучшить mobile-поведение (узкие экраны):

- действия/кнопки не должны налезать;
- поля API key / quick tools корректно переносятся;
- списки и ленты не ломают layout.

1. Добавить defensive CSS для длинных токенов/ссылок (word-break/overflow-wrap).
1. Не менять JS-контракт и ID.
1. Добавить FRONTEND_R4_MOBILE_QA_RU.md:

- 10 проверок (desktop/mobile),
- критерии PASS/FAIL.

## Обязательные проверки

- python3 scripts/validate_web_prototype_compat.py --base src/web/index.html --prototype src/web/index.html
- python3 scripts/validate_web_runtime_parity.py --base src/web/index.html --prototype src/web/index.html

## Формат сдачи

1. Изменённые файлы
2. Что улучшено по mobile
3. Команды проверок
4. Результаты
5. Остаточные риски
