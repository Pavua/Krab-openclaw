---
name: krab-release-gate-keeper
description: "Собирать merge-gate и release verdict для проекта `/Users/pablito/Antigravity_AGENTS/Краб` на основе unit, smoke, e2e и фактических артефактов. Использовать, когда пользователь просит понять, готова ли ветка к merge/release, какие есть блокеры, какие проверки обязательны и что ещё остаётся непокрытым."
---

# Krab Release Gate Keeper

Используй этот навык, когда нужен честный вердикт по готовности. Сначала перечисляй проверки и блокеры, затем кратко формулируй итог.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Базовый порядок

1. Понять, это локальный gate для одной правки или полный pre-release прогон.
2. Запустить минимальный набор unit/integration для затронутой области.
3. Запустить smoke и merge-gate скрипты.
4. Если задача затрагивает live transport/UI, добавить live smoke.
5. Сформулировать вердикт в формате: блокеры, остаточные риски, что подтверждено, что не проверено.

## Основные команды

```bash
python3 scripts/pre_release_smoke.py
./pre_release_smoke.command
python3 scripts/r20_merge_gate.py
./scripts/r20_merge_gate.command
python3 scripts/live_channel_smoke.py --max-age-minutes 60
pytest -q
```

## Источники статуса

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/output/reports/`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/`

## Ограничения

- Не писать `готово к merge`, если есть непокрытый обязательный сценарий.
- Не смешивать passed unit и неподтверждённый live behavior.
- Если пользователь просит review, выводить findings первыми.
- Явно помечать, какие проверки не запускались.
