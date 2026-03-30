---
name: krab-swarm-smoke-ops
description: "Прогонять и отлаживать swarm smoke и swarm live smoke контур проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая AgentRoom, mock/live режимы и loop-поведение. Использовать, когда нужно проверить, что `!agent swarm` работает, воспроизвести сбой в swarm orchestration, подтвердить smoke перед ручным запуском swarm-команд или оценить cloud/live устойчивость роя."
---

# Krab Swarm Smoke Ops

Используй этот навык для swarm-контура, когда нужно быстро понять, жив ли AgentRoom и его orchestration. Начинай с mock smoke, live режим подключай только если он действительно нужен.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/swarm_test_script.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/swarm_live_smoke.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_swarm_smoke.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_swarm_live_smoke.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/swarm.py`

## Рабочий цикл

1. Прогнать mock swarm smoke.
2. Проверить, что round/loop orchestration и формат ответа не сломаны.
3. Если задача про реальный канал, запустить live smoke.
4. Зафиксировать отчёт и отдельно отметить, это mock или live.

## Полезные тесты

```bash
pytest tests/unit/test_command_handlers_agent_swarm.py -q
pytest tests/test_r17_agent_room.py -q
```

## Ограничения

- Не запускать live swarm без причины, если mock уже локализует регресс.
- Не смешивать ошибки orchestration и ошибки transport/provider layer.
- Если live smoke использует cloud, явно отмечать это в выводе.
