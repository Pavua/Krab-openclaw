# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R8)

Работаем строго в frontend/design ownership.
Модель: Gemini 3.1 Pro (High) + Nano Banana Pro для визуального polish.

## Цель

Подготовить nano-прототипы к продуктовой интеграции:

1. унифицировать interaction-patterns между 3 экранами,
2. усилить адаптивность и визуальную иерархию данных,
3. подготовить handoff-пакет для быстрой интеграции Codex.

## Разрешенные файлы

- `src/web/prototypes/nano/index_redesign.html`
- `src/web/prototypes/nano/transcriber_console.html`
- `src/web/prototypes/nano/ops_center.html`
- `src/web/prototypes/nano/nano_theme.css`
- `docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md`
- `docs/frontend_ui_polish/FRONTEND_R8_HANDOFF_RU.md` (новый)

## Что сделать

1. Interaction consistency:

- привести button/field/status паттерны к одному поведению на всех трех страницах;
- одинаковые hover/focus/disabled/active состояния;
- единый визуальный стиль warning/error/success.

1. Data readability:

- улучшить отображение длинных метрик/идентификаторов (ellipsis + wrap policy);
- добавить четкую иерархию заголовков и вторичных меток;
- улучшить читаемость журналов/логов в `ops_center`.

1. Responsive polish:

- проверить и исправить breakpoints для диапазонов `<390`, `<430`, `<768`;
- исключить горизонтальный скролл в рабочих блоках;
- сохранить устойчивое расположение CTA-кнопок.

1. Handoff doc:

- создать `FRONTEND_R8_HANDOFF_RU.md`:
  - какие стили общие,
  - какие блоки первыми интегрировать,
  - где риски визуальной регрессии,
  - короткий checklist перед промоушеном.

## Ограничения

1. Не менять `src/web/index.html`.
2. Не менять Python/backend.
3. Не добавлять JS-framework.

## Обязательные проверки

- `python3 scripts/check_workstream_overlap.py`
- `python3 scripts/validate_web_prototype_compat.py --base src/web/index.html --prototype src/web/prototypes/nano/index_redesign.html`

## Формат сдачи

1. Изменённые файлы
2. Что улучшено по interaction/readability/responsive
3. Команды проверки
4. Результаты
5. Риски/ограничения
