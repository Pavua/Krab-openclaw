# AG Prompt R21 Frontend — Ops Observatory (большой UI-блок)

Контекст:
- Проект: `/Users/pablito/Antigravity_AGENTS/Краб`
- Основной файл: `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
- Уже есть блок Core Health (lite/deep).
- Backend в R21 добавляет:
  - `GET /api/ops/reports/catalog`
  - `GET /api/ops/reports/latest/{report_id}`

## Цель
Сделать в Web Panel отдельный крупный блок наблюдаемости OPS-репортов, чтобы оператор видел качество сборки/health без чтения файлов вручную.

## Задача

### 1) Новый UI-блок `Ops Observatory`
Добавить карточки для:
- `r20_merge_gate`
- `krab_core_health_watch`
- `live_channel_smoke`
- `lmstudio_idle_guard`
- `pre_release_smoke`

Для каждой карточки показывать минимум:
- статус (`ok`/`fail`/`no data`),
- время генерации,
- ключевые счётчики (если есть: `required_failed`, `advisory_failed`, `up/down`, `flaps`, `findings`).

### 2) JS-логика
1. `loadOpsReportsCatalog()` — загрузка каталога.
2. `loadOpsReport(reportId)` — загрузка latest payload.
3. `renderOpsReportCard(reportId, payload)`.
4. Auto-refresh каждые 30с + ручная кнопка Refresh.
5. Если report отсутствует/битый — аккуратный fallback, UI не падает.

### 3) UX требования
1. Блок не должен ломать текущие секции страницы.
2. Адаптивность desktop/mobile сохранить.
3. Визуально использовать существующую систему стилей (без "нового дизайна с нуля").

### 4) Smoke
1. Открыть `http://127.0.0.1:8080`.
2. Проверить загрузку нового блока.
3. Проверить fallback поведение (минимум для одного отсутствующего report).
4. Приложить 1-2 скриншота.

## Ограничения
1. Не трогать backend-файлы.
2. Не менять существующие API-вызовы других блоков.

## Формат ответа
1. Измененные файлы.
2. Что реализовано.
3. Шаги/команды smoke.
4. Риски/ограничения.
