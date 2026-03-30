---
name: krab-owner-fastpath-diagnostics
description: "Проверять deterministic owner fast-path для диагностических и capability-вопросов в проекте `/Users/pablito/Antigravity_AGENTS/Краб`, особенно запросы вроде `проведи полную диагностику` и `cron у тебя уже работает`. Использовать, когда нужно убедиться, что owner-диагностика идёт в truthful self-check без свободной LLM-генерации, локализовать регресс fast-path или подтвердить, что capability/status ответы отражают реальный runtime."
---

# Krab Owner Fastpath Diagnostics

Используй этот навык для owner-вопросов, которые должны обходить свободную генерацию и идти в truth-layer. Это отдельный слой диагностики, а не просто “ещё один help”.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_capability_truth.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`

## Рабочий цикл

1. Определить, относится ли запрос к capability/status/self-check fast-path.
2. Проверить, что он не проваливается в свободную LLM-генерацию.
3. Подтвердить, что ответ строится из реального runtime state.
4. Если есть регресс, локализовать условие маршрутизации в `src/userbot_bridge.py`.

## Полезные тесты

```bash
pytest tests/unit/test_userbot_capability_truth.py -q
```

## Ограничения

- Не подменять truthful fast-path красивым, но непроверенным текстом.
- Не расширять fast-path на произвольные вопросы без необходимости.
- Если ответ зависит от runtime, сначала проверить runtime state.
