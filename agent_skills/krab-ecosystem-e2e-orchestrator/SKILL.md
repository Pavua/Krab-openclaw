---
name: krab-ecosystem-e2e-orchestrator
description: "Оркестрировать полный ecosystem e2e для проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая one-click start/stop/check, live smoke, restart и связку соседних сервисов. Использовать, когда нужно проверить стек целиком после изменений, убедиться, что Krab, OpenClaw, Voice Gateway, Krab Ear и вспомогательные контуры поднимаются в правильной последовательности и проходят интеграционную верификацию."
---

# Krab Ecosystem E2e Orchestrator

Используй этот навык для проверки стека целиком, а не отдельного сервиса. Его задача: прогнать полный жизненный цикл `start -> check -> smoke -> restart -> recheck`.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/Start Full Ecosystem.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Stop Full Ecosystem.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Check Full Ecosystem.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/live_ecosystem_e2e.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_ecosystem_e2e.py`

## Рабочий цикл

1. Поднять стек через one-click launcher или эквивалентный безопасный путь.
2. Проверить health ключевых endpoints и сервисов.
3. Запустить live ecosystem e2e.
4. При необходимости сделать controlled restart и повторную проверку.
5. Сформулировать итог отдельно по каждому сервису и по общему стеку.

## Ограничения

- Не объявлять ecosystem e2e зелёным, если зелёный только один сервис.
- Не путать “процессы запущены” с “интеграция работает”.
- После restart проверять повторно, а не использовать старый smoke-результат.
