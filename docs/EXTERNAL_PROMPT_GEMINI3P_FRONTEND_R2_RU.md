# EXTERNAL PROMPT — GEMINI 3.1 PRO (FRONTEND R2)

Работаем в потоке `frontend/design`.
Модель: **Gemini 3.1 Pro (High)**.

## Контекст

У нас уже есть прототип:

- `src/web/prototypes/nano/index_redesign.html`

Совместимость по ID уже пройдена, но внизу остаётся placeholder-логика JS (демо-скрипт), а не полный рабочий runtime из боевого `src/web/index.html`.

## Цель R2

Сделать **production-ready frontend-кандидат**, который визуально остается в новом стиле, но по JS-поведению полностью соответствует боевому `src/web/index.html`.

## Жесткие ограничения

1. Не менять backend/Python.
2. Работать только в frontend-файлах/доках.
3. Не удалять обязательные ID/контролы.
4. Не оставлять mock/demo/placeholder JS.

## Что сделать

1. Взять полный рабочий JS-контур из `src/web/index.html`.
1. Интегрировать его в редизайн (`src/web/prototypes/nano/index_redesign.html`) без потери визуального оформления.
1. Убедиться, что:

- все прежние обработчики и API-вызовы сохранены;
- нет строк/комментариев вида `Placeholder`, `Mock`, `Simulating`;
- все обязательные ID на месте.

1. Подготовить короткий интеграционный чеклист R2.

## Разрешенные файлы для изменений

- `src/web/prototypes/nano/index_redesign.html`
- `docs/frontend_design/INTEGRATION_PLAN_RU.md`
- `docs/frontend_design/INTEGRATION_CHECKLIST_R2_RU.md` (новый)

## Обязательная самопроверка

Запустить:

- `python3 scripts/validate_web_prototype_compat.py --base src/web/index.html --prototype src/web/prototypes/nano/index_redesign.html`

Ожидается PASS:

- missing ids: 0
- mock markers: 0

## Формат сдачи

В конце выдай:

1. Список изменённых файлов.
2. Точные команды проверки.
3. Итог проверок (PASS/FAIL).
4. Короткий риск-лист (если остался).
