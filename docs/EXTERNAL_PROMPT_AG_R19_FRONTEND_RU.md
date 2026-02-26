# R19 FRONTEND STREAM (Antigravity)

Работаешь в репозитории:
`/Users/pablito/Antigravity_AGENTS/Краб`

## Цель

Усилить Web Panel как операционный центр:
1. стабильные состояния health/queue без визуальных флапов;
2. понятная anti-413 зона с артефактами;
3. прозрачная диагностика runtime-ошибок для оператора.

## Жёсткие ограничения

1. Не ломай существующие `id`/селекторы, которые уже используются в smoke/Playwright.
2. Не меняй write-API контракты backend.
3. Только аккуратный UI+JS слой; backend менять только если без этого невозможно read-only данные отрисовать.
4. Никаких моков вместо реальных данных.

## Файлы (основные)

- `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
- при необходимости (read-only endpoint):
  - `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- тесты при необходимости:
  - `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`

## Пакет задач (большой, одним заходом)

### 1) Health/Queue статус без флапов

Сделай в панели операционный статус с явными состояниями:
- `UP` (зелёный),
- `DEGRADED` (жёлтый),
- `DOWN` (красный).

Логика:
- если последний успешный health был недавно, а текущий запрос кратковременно упал, не мигать сразу в `DOWN`;
- показать `last_success_at` и `last_error_at`;
- если процесс активен, но HTTP нестабилен — показывать `DEGRADED` с пояснением.

### 2) Anti-413 Recovery UX polish

Для блока anti-413:
- явно показывай 3 состояния кнопок: `idle/loading/done`;
- добавь компактный history (последний checkpoint и последний transition pack);
- ссылки на артефакты отображай как кликабельные элементы;
- если endpoint вернул ошибку — короткий actionable-текст (без raw traceback).

### 3) Runtime diagnostics widget

Добавь мини-виджет диагностики:
- `channels_probe` (ok/fail),
- `error_findings_count`,
- `warn_findings_count`,
- последняя дата smoke-отчёта.

Источник данных:
- если есть read endpoint для latest smoke, используй его;
- если endpoint нет, добавь минимальный read-only endpoint в `web_app.py` (без write доступа).

### 4) Мобильная и desktop консистентность

- Проверь адаптивность на узком экране.
- Убери переполнения длинных путей (ellipsis + tooltip/title).
- Сохрани существующий стиль проекта (без радикального редизайна).

## Acceptance Criteria

1. Визуальный флап статусов уменьшен, статусы понятны оператору.
2. Anti-413 блок показывает чёткий жизненный цикл операций.
3. Новый diagnostics-виджет работает и не блокирует UI.
4. Нет регресса старых кнопок/обработчиков.

## Проверка (обязательно)

1. `pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_web_app.py`
2. Browser smoke (Playwright CLI):
   - открыть `http://127.0.0.1:8080`
   - проверить блоки status/anti-413/diagnostics
   - сохранить screenshot в `output/playwright/`

## Формат сдачи

1. `git diff --name-only`
2. Короткий changelog (что добавлено/изменено)
3. Точные команды проверок и результат
4. 1-2 скриншота итогового UI
5. Риски/что осталось за рамками
