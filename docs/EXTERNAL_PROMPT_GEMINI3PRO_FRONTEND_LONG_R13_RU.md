# EXTERNAL PROMPT — GEMINI 3 PRO (FRONTEND LONG R13)

## Контекст
Длинный frontend-цикл для Krab Web Panel V2. Цель: сделать панель полноценным операционным пультом (наблюдаемость, управление, понятные состояния), без изменения backend-контрактов.

## Жёсткие границы
1. Не менять Python/backend (`src/**/*.py`).
2. Не ломать существующие `id` и JS-hook точки.
3. Не удалять рабочие элементы управления; только улучшать UX/визуал/связность.
4. Не добавлять фейковые данные и мок-статусы в боевой файл.

## Основные файлы
- `src/web/index.html`
- `src/web/prototypes/nano/index_redesign.html`
- `src/web/prototypes/nano/nano_theme.css` (если нужно)

## Цели длинного цикла (R13)

### Этап A — Ops Cockpit UX
1. Усилить верхние операционные блоки:
- единые badge-статусы (`OK/WARN/FAIL/LOADING`);
- одинаковые паттерны отображения для system/local model/channels/autoswitch;
- явный timestamp последнего успешного sync.

2. Улучшить блок Ops Alerts:
- фильтр/поиск по code;
- более читаемая история событий;
- выделение acknowledged/revoked состояний.

3. Поведение при ошибках API:
- всегда показывать какая операция упала;
- всегда показывать `detail`/`error` (если есть);
- не оставлять интерфейс в «подвисшем» состоянии.

### Этап B — Control Center & Assistant Flow
1. OpenClaw Control Center:
- визуально связать действия (`Set Local/Auto/Cloud`, `Apply Autoswitch`, `Refresh`) с результатом;
- после операции — четкий feedback (success/warn/error).

2. AI Assistant Interface:
- улучшить читаемость формы и output-панели;
- понятные состояния выполнения (`готов`, `выполняется`, `ошибка`);
- аккуратные переносы длинного текста и логов.

3. Quick Utilities:
- выровнять сетку;
- мобильная адаптация для 768/430/390.

### Этап C — Runtime Safety & Protocol UX
1. File protocol guard:
- если `file://` — заметный и нераздражающий banner с кнопкой/инструкцией запуска `http://127.0.0.1:8080`.

2. Защита от double-init:
- убедиться, что UI init/handlers не дублируются при повторном sync или частичном refresh.

3. Проверить, что UX не ломает существующие сценарии ручного управления моделью.

### Этап D — Visual Polish (без отрыва от прод)
1. Подтянуть типографику, интервалы, контраст, состояния кнопок/inputs.
2. Привести логический стиль секций к единой визуальной системе.
3. Обновить `index_redesign.html` в рамках runtime parity, не расходясь с боевым поведением.

## Проверки (обязательно)
1. `scripts/validate_web_prototype_compat.command`
2. `python3 scripts/validate_web_runtime_parity.py --base src/web/index.html --prototype src/web/prototypes/nano/index_redesign.html`
3. `python3 scripts/check_workstream_overlap.py`

## Формат финального отчёта
1. Что сделано по этапам A/B/C/D.
2. Список изменённых frontend-файлов.
3. Какие блоки/ID были затронуты.
4. Результаты всех проверок (команды + итог).
5. Остаточные UX-риски и рекомендации.

## Важно
Если не хватает backend endpoint для UX-фичи — не имитируй его на фронте, отметь как blocker и опиши требуемый контракт.
