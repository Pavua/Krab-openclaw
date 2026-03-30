---
name: krab-core-health-watch-operator
description: "Проверять core health watch, watchdog и ops guard контуры проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая свежие JSON-отчёты в `artifacts/ops`. Использовать, когда нужно быстро понять деградацию ядра, собрать последние health-watch evidence, отладить `krab_core_health_watch.py` или `openclaw_ops_guard.py`, либо подтвердить, что периодический watchdog-слой видит ту же проблему, что и runtime/UI."
---

# Krab Core Health Watch Operator

Используй этот навык для health-watch и watchdog слоя, когда нужно быстро получить независимую картину состояния ядра. Он полезен как cross-check к runtime и owner UI.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/krab_core_health_watch.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/krab_core_health_watch.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_ops_guard.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/krab_core_health_watch_latest.json`

## Полезные тесты

```bash
pytest tests/test_krab_core_health_watch.py -q
```

## Рабочий цикл

1. Запустить или прочитать последний health-watch report.
2. Сопоставить его с текущим runtime состоянием.
3. Если health-watch и runtime расходятся, фиксировать это как отдельную проблему истины.
4. При необходимости прогнать ops guard и сравнить рекомендации.

## Ограничения

- Не считать старый `latest.json` достаточным без оценки его свежести.
- Не смешивать watchdog warning и подтверждённый runtime failure.
- Если health watch зелёный, а live smoke красный, доверять более близкому к симптому сигналу.
