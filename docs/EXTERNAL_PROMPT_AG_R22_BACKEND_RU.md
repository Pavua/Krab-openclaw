# AG Prompt R22 Backend — Control Compatibility + Routing Source of Truth

Контекст:

- Проект: `/Users/pablito/Antigravity_AGENTS/Краб`
- Есть периодические предупреждения в OpenClaw Control UI вида `Unsupported schema node. Use Raw mode.`
- По CLI (`openclaw channels status --probe`) runtime каналов рабочий.
- Нужно не «чинить чужой UI вслепую», а дать прозрачную диагностику и единый источник истины в Krab API.

## Блок 1. Control compatibility diagnostics API

### Требования

1. Добавить в `src/modules/web_app.py` endpoint:
   - `GET /api/openclaw/control-compat/status`

2. Endpoint должен возвращать структурированный JSON:
   - `ok: bool`
   - `runtime_channels_ok: bool`
   - `control_schema_warnings: list[str]`
   - `impact_level: "none" | "ui_only" | "runtime_risk"`
   - `recommended_action: str`

3. Источники данных:
   - текущий runtime статус каналов (через существующую логику/CLI обертку),
   - последние логи OpenClaw (анализ на маркеры `Unsupported schema node`, `schema`, `validation` и т.п.).

4. Если runtime жив, но есть schema warning — `impact_level = "ui_only"`.

5. Никакого write-auth (read-only endpoint).

## Блок 2. Routing effective source API

### Требования

1. Добавить endpoint:
   - `GET /api/openclaw/routing/effective`

2. Возвращать:
   - `ok`
   - `force_mode_requested`
   - `force_mode_effective`
   - `assistant_default_slot`
   - `assistant_default_model`
   - `cloud_fallback_enabled`
   - `decision_notes` (короткий список объяснений: почему route пошел local/cloud)

3. Использовать уже существующие зависимости роутера/каталога, без дублирования логики в endpoint.

## Блок 3. Тесты

### Требования

1. Добавить/обновить тесты в `tests/test_web_app.py`:
   - `test_openclaw_control_compat_status_ui_only_warning`
   - `test_openclaw_routing_effective_endpoint`

2. Моки делать в стиле текущих тестов (без реальных вызовов внешних сервисов).

3. Прогнать targeted pytest и приложить фактический вывод.

## Блок 4. Документация

### Требования

1. Обновить `HANDOVER.md`:
   - зачем введены новые endpoint’ы,
   - какие инциденты закрывают,
   - команды проверки.

2. Коротко обновить `docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md` (если раздела нет — добавить):
   - `Unsupported schema node` как трактовать,
   - когда это UI-only, а когда runtime risk.

## Ограничения

1. Не менять существующий контракт текущих endpoint’ов.
2. Не трогать unrelated файлы.
3. Комментарии/docstring только на русском.

## Формат ответа

1. Список измененных файлов.
2. Краткий diff-summary.
3. Команды тестов + фактический вывод.
4. Остаточные риски.
