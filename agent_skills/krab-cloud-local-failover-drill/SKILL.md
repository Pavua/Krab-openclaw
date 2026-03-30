---
name: krab-cloud-local-failover-drill
description: "Проверять и отрабатывать failover между облачными и локальными моделями в проекте `/Users/pablito/Antigravity_AGENTS/Краб`, включая routing policy, autoswitch, LM Studio и cloud provider readiness. Использовать, когда нужно доказать, что cloud/local recovery route работает, локализовать ложный failover, подтвердить fallback chain или воспроизвести регресс в local-first/cloud-first поведении."
---

# Krab Cloud Local Failover Drill

Используй этот навык, когда надо доказать цепочку деградации и восстановления между cloud и local. Цель не в том, чтобы просто “вызвать ошибку”, а в том, чтобы подтвердить корректный fallback/recovery path.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/check_cloud_chain.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_model_autoswitch.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/LM Studio Status.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Switch OpenClaw Cloud First.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Switch OpenClaw Local First.command`

## Рабочий цикл

1. Снять текущее cloud/local routing состояние.
2. Проверить доступность cloud route и local route по отдельности.
3. Воспроизвести failover или simulated degradation.
4. Подтвердить, что fallback chain и recovery path соответствуют ожиданиям.
5. Отдельно зафиксировать, какой контур стал primary после проверки.

## Полезные тесты

```bash
pytest tests/integration/test_cloud_failover_chain.py -q
pytest tests/unit/test_openclaw_model_autoswitch.py -q
```

## Ограничения

- Не делать вывод о failover по одному только конфигу без фактического route status.
- Не путать “cloud недоступен” и “local не готов”.
- После drill вернуть primary в ожидаемое состояние или явно зафиксировать смену.
