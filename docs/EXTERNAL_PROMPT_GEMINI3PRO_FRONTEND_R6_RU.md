# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R6)

Работаем строго в frontend/design ownership.
Модель: Gemini 3.1 Pro (High) + Nano Banana Pro для визуального polish.

## Цель

Подготовить интерфейсы к “integration-ready handoff”:

1. повысить mobile-устойчивость и доступность (a11y),
2. унифицировать UI-состояния в nano-прототипах,
3. подготовить визуальный QA-пакет для быстрой приемки.

## Разрешенные файлы

- `src/web/prototypes/nano/index_redesign.html`
- `src/web/prototypes/nano/transcriber_console.html`
- `src/web/prototypes/nano/ops_center.html`
- `src/web/prototypes/nano/nano_theme.css`
- `docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md`
- `docs/frontend_ui_polish/FRONTEND_R6_QA_MATRIX_RU.md` (новый)

## Что сделать

1. Mobile + overflow hardening:
   - экраны `<390px` и `<430px` не должны давать горизонтальный скролл;
   - длинные токены/ID/URL корректно переносятся (`overflow-wrap`, `word-break`);
   - кнопки в toolbars не налезают друг на друга.

2. A11y baseline:
   - видимый `focus-visible` для интерактивных элементов;
   - контрастные состояния для `error/warn/success/info`;
   - минимальные `aria-label` там, где смысл кнопки неочевиден.

3. Unified states:
   - привести к единой схеме state-бейджей/панелей:
     - `loading`,
     - `empty`,
     - `error`,
     - `ready`;
   - синхронизировать визуально между `index_redesign`, `transcriber_console`, `ops_center`.

4. Документация:
   - создать `docs/frontend_ui_polish/FRONTEND_R6_QA_MATRIX_RU.md`
     - минимум 14 проверок;
     - отдельные секции desktop/mobile/a11y;
     - формат PASS/FAIL + критерий для каждой проверки.

## Ограничения

1. Не менять `src/web/index.html`.
2. Не менять Python/backend.
3. Не добавлять фреймворки/билд-систему.

## Обязательные проверки

- `python3 scripts/check_workstream_overlap.py`
- `python3 scripts/validate_web_prototype_compat.py --base src/web/index.html --prototype src/web/prototypes/nano/index_redesign.html`

## Формат сдачи

1. Изменённые файлы
2. Что улучшено (mobile/a11y/states)
3. Команды проверки
4. Результаты
5. Риски/ограничения
