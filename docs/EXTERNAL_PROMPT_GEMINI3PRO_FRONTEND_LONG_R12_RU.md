# EXTERNAL PROMPT — GEMINI 3 PRO (FRONTEND LONG R12)

## Контекст
Это длинный frontend-цикл (одним запуском) для Krab Web Panel. Задача: довести UX/операционность панели до уровня «ежедневный пульт управления».

## Жёсткие границы
1. Не менять Python/backend (`src/**/*.py`) — только frontend.
2. Не ломать существующие id/JS-hook точки.
3. Не вырезать уже существующие рабочие блоки.

## Основные файлы
- `src/web/index.html`
- `src/web/prototypes/nano/index_redesign.html`
- `src/web/prototypes/nano/nano_theme.css` (если нужно)

## Цели длинного цикла (R12)

### Этап A — Control Center UX Hardening
1. Укрепить блок OpenClaw Control Center:
- наглядные badges: `OK/WARN/FAIL`;
- единый стиль статусов для autoswitch/channels/local lifecycle;
- видимые состояния loading для каждой операции.

2. Улучшить ошибки:
- показывать `detail` от API;
- показывать, какая операция упала (`load local`, `apply autoswitch`, etc.).

3. Проверить file-protocol warning:
- если `window.location.protocol === 'file:'` — видимый banner с инструкцией на `http://127.0.0.1:8080`.

### Этап B — Assistant/Tools Usability
1. Подчистить блок AI Assistant:
- читаемость формы на мобильном (~390px);
- минимизировать визуальный шум;
- явный статус `assistantMeta` + понятные fallback сообщения.

2. Quick Utilities:
- выровнять ширины кнопок/полей;
- сделать аккуратный перенос на узких экранах.

### Этап C — Prototype Parity & Safety
1. Синхронизировать `index_redesign.html` с боевым `index.html` по обязательным id.
2. Убедиться, что compatibility/runtime parity скрипты проходят.
3. Не добавлять мок-маркеры или фальшивые placeholder-функции.

### Этап D — Visual Polish (Nano theme)
1. Точечно улучшить типографику, контраст и отступы, без поломки логики.
2. Проверить states кнопок (`hover/disabled/active`) и таблиц/логов.

## Проверки (обязательно)
1. `scripts/validate_web_prototype_compat.command`
2. `python3 scripts/validate_web_runtime_parity.py --base src/web/index.html --prototype src/web/prototypes/nano/index_redesign.html`
3. `python3 scripts/check_workstream_overlap.py`

## Формат финального отчёта
1. Что сделано по этапам A/B/C/D.
2. Какие id/блоки добавлены или изменены.
3. Какие API endpoint используются в UI.
4. Результаты проверок (команды + итог).
5. Остаточные риски UX.

## Важно
Если нужен backend-эндпоинт, которого нет — не импровизируй на frontend: отметь как blocker и предложи контракт для backend.
