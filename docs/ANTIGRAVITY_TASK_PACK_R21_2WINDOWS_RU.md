# Antigravity Task Pack R21 (2 окна, крупные блоки)

## Цель спринта
После стабилизации R20 сделать следующий шаг: 
1) снизить шум/нагрузку deep-health контура,
2) добавить наблюдаемость по артефактам OPS прямо в Web Panel,
3) сохранить нулевой регресс по текущим API/скриптам.

## Окно A — Frontend (большой блок)
Запустить prompt:
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R21_FRONTEND_RU.md`

## Окно B — Backend (большой блок)
Запустить prompt:
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R21_BACKEND_RU.md`

## Контракт между окнами (фиксируем заранее)
1. Backend добавляет read-only API:
   - `GET /api/ops/reports/catalog`
   - `GET /api/ops/reports/latest/{report_id}`
2. Поддерживаемые `report_id`:
   - `r20_merge_gate`
   - `krab_core_health_watch`
   - `live_channel_smoke`
   - `lmstudio_idle_guard`
   - `pre_release_smoke`
3. Frontend использует только эти endpoint'ы + уже существующие:
   - `/api/health/lite`
   - `/api/health`

## Общие ограничения
1. Ветка: `codex/queue-forward-reactions-policy`.
2. Не трогать unrelated файлы.
3. Комментарии/docstring в коде только на русском.
4. Никаких destructive git-команд.
5. В конце каждого окна дать:
   - список файлов,
   - фактические команды проверок,
   - фактический вывод тестов,
   - остаточные риски.

## Definition of Done
1. Оба окна закрывают свои блоки независимо.
2. `pytest` по изменённым зонам зелёный.
3. `python3 scripts/r20_merge_gate.py` проходит после интеграции.
4. Web Panel показывает OPS-репорты без падений даже при отсутствии части файлов.
5. Никаких изменений, ломающих текущие R20 endpoint-контракты.
