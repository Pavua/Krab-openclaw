# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R3)

Работаем строго в frontend-контуре.
Модель: Gemini 3.1 Pro (High).

## Контекст
В проекте уже есть:
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/transcriber_console.html
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/ops_center.html
- /Users/pablito/Antigravity_AGENTS/Краб/docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md

## Цель R3
Сделать чистую frontend-упаковку без дублирования стилей:
1) вынести общую Nano-тему в единый CSS,
2) подключить её во все 3 прототипа,
3) сохранить текущую визуальную идентичность и responsive-поведение.

## Жесткие ограничения
1. Не трогать Python/backend.
2. Не менять API-контракт и DOM id в index_redesign.
3. Не использовать внешние CSS/JS framework.

## Что сделать
1. Создать общий стиль-файл:
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/nano_theme.css

2. Рефакторнуть прототипы:
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/transcriber_console.html
- /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/ops_center.html

Требование: в каждом файле оставить только page-specific стили; общие токены/базовые компоненты вынести в nano_theme.css.

3. Обновить доку:
- /Users/pablito/Antigravity_AGENTS/Краб/docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md
Добавить раздел «Подключение nano_theme.css».

4. Добавить краткий integration note:
- /Users/pablito/Antigravity_AGENTS/Краб/docs/frontend_design/FRONTEND_R3_PACKAGING_NOTE_RU.md

## Обязательные проверки
1) Совместимость прототипа:
python3 /Users/pablito/Antigravity_AGENTS/Краб/scripts/validate_web_prototype_compat.py --base /Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html --prototype /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html

2) Runtime parity:
python3 /Users/pablito/Antigravity_AGENTS/Краб/scripts/validate_web_runtime_parity.py --base /Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html --prototype /Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html

## Формат сдачи
- Изменённые файлы
- Команды проверок
- Результаты проверок
- Короткие риски
