---
name: krab-incident-runbook-executor
description: "Проводить инцидентный разбор и исполнение runbook для проекта `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда runtime молчит, transport не доставляет сообщения, UI показывает ложный status, сломаны model routes, падают alert routes или нужен быстрый, но аккуратный operational response с артефактами и верификацией."
---

# Krab Incident Runbook Executor

Используй этот навык как operational режим для инцидентов. Сначала сохраняй следы, затем локализуй, затем чини точечно.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Классифицировать инцидент: runtime, transport, UI, routing, policy, LM Studio, MCP.
2. Снять snapshot, health и релевантные логи.
3. Зафиксировать точное время и симптом.
4. Применить минимальный достаточный repair.
5. Перепроверить той же дорогой, которой инцидент проявлялся.
6. Сформулировать краткий postmortem: причина, действие, текущий статус, остаточный риск.

## Полезные источники

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/ops_incident_runbook.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/`
- `/Users/pablito/Antigravity_AGENTS/Краб/logs/`
- `/Users/pablito/Antigravity_AGENTS/Краб/output/reports/`

## Ограничения

- Не чинить до сбора первичных артефактов, если это не делает ситуацию необратимо хуже.
- Не писать “инцидент закрыт”, пока не воспроизведён положительный сценарий проверки.
- Не раздувать отчёт; фиксировать только наблюдаемое и проверенное.
