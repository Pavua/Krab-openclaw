# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R5)

Работаем строго в frontend/design контуре.
Модель: Gemini 3.1 Pro (High) + Nano Banana Pro (внутри Gemini) для визуальной части.

## Цель

Довести дополнительные интерфейсы до интеграционного уровня:

1. `transcriber_console` и `ops_center` должны выглядеть как единая продуктовая система,
2. добавить явные состояния UX (loading/empty/error/success),
3. подготовить короткий acceptance-checklist для последующей интеграции.

## Разрешенные файлы

- `src/web/prototypes/nano/transcriber_console.html`
- `src/web/prototypes/nano/ops_center.html`
- `src/web/prototypes/nano/nano_theme.css`
- `docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md`
- `docs/frontend_ui_polish/TRANSCRIBER_UI_SPEC_RU.md`
- `docs/frontend_ui_polish/OPS_CENTER_UI_SPEC_RU.md`
- `docs/frontend_ui_polish/FRONTEND_R5_ACCEPTANCE_CHECKLIST_RU.md` (новый)

## Жесткие ограничения

1. Не менять `src/web/index.html`.
2. Не ломать существующие ID/классы в уже сверстанных блоках без необходимости.
3. Никаких mock-данных, которые выглядят как прод-ответы API.

## Что сделать

1. `transcriber_console.html`:

- привести карточки/панели к единому дизайн-языку с `nano_theme.css`;
- добавить 4 состояния: loading, empty, error, ready;
- улучшить мобильную версию (<420px): без горизонтального скролла и наложения контролов.

1. `ops_center.html`:

- усилить визуальную иерархию метрик/алертов/действий;
- добавить состояния для отсутствия данных и аварийного режима;
- улучшить читаемость длинных значений (wrap/ellipsis/tooltip-friendly styling).

1. `nano_theme.css`:

- добавить недостающие токены/utility-классы для состояний (success/warn/error/info);
- унифицировать отступы/типографику между двумя экранами.

1. Документация:

- создать `docs/frontend_ui_polish/FRONTEND_R5_ACCEPTANCE_CHECKLIST_RU.md`:
  - минимум 12 проверок (desktop/mobile),
  - формат PASS/FAIL,
  - отдельный блок «что проверить перед промоушеном в index».

## Обязательные проверки

- `python3 scripts/check_workstream_overlap.py`

## Формат сдачи

1. Изменённые файлы
2. Что улучшено в transcriber/ops center
3. Какие состояния UX добавлены
4. Команда проверки и результат
5. Риски/ограничения
