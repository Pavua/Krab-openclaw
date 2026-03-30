---
name: krab-ops-observability-console
description: "Проверять operational observability проекта `/Users/pablito/Antigravity_AGENTS/Краб`: usage, alerts, costs, ops history и агрегированные ops endpoints owner UI. Использовать, когда нужно быстро понять текущее состояние бюджета, алертов и истории операций, подтвердить truthful ops summary, расследовать деградацию по alerts/history или подготовить короткий operational digest без ручного чтения разрозненных JSON."
---

# Krab Ops Observability Console

Используй этот навык для верхнеуровневой operational картины. Его задача: быстро собрать truthful summary по usage, alerts, costs и history, не читая вручную весь хвост артефактов.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые endpoints

- `GET /api/ops/usage`
- `GET /api/ops/alerts`
- `GET /api/ops/history`
- агрегированный ops report через web router compat

## Ключевые файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_router_compat.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/cost_analytics.py`

## Рабочий цикл

1. Считать usage/alerts/history из API.
2. Если есть `no_usage_yet`, не переинтерпретировать это как ошибку.
3. Сопоставить alerts с runtime состоянием и history.
4. Если нужен report, опираться на API и свежие ops-артефакты, а не на UI-впечатление.

## Ограничения

- Не считать отсутствие usage доказательством поломки.
- Не смешивать stale ops history и актуальные alert-события.
- Если alert подтверждён или очищен, проверить это повторным чтением endpoint-а.
