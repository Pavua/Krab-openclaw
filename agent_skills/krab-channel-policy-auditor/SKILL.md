---
name: krab-channel-policy-auditor
description: "Аудировать channel policies, allowlist-правила и alert routes в проекте `/Users/pablito/Antigravity_AGENTS/Краб`, особенно для Telegram, Signal и связанных каналов. Использовать, когда нужно проверить reserve-safe режим, убрать wildcard-дыры, понять почему alert route не срабатывает, восстановить безопасные `dmPolicy/groupPolicy` или подтвердить корректность allowlist-конфигурации."
---

# Krab Channel Policy Auditor

Используй этот навык для проверки реальной безопасности каналов и оповещений. Главная цель: не допустить “случайно открытого” контура под видом рабочего.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Считать текущие `dmPolicy`, `groupPolicy`, allowlist и alert routes.
2. Проверить, нет ли wildcard или пустых allowlist, которые делают политику ложнобезопасной.
3. Прогнать релевантные repair или route-check утилиты.
4. Если правка применена, перепроверить конфиг и тест отправки alert.

## Основные точки входа

```bash
./Apply Reserve Telegram Policy.command
python3 scripts/openclaw_runtime_repair.py
./scripts/check_signal_alert_route.command
./scripts/configure_alert_route.command
./scripts/resolve_telegram_alert_target.command
./scripts/signal_alert_test.command
pytest tests/unit/test_openclaw_runtime_repair.py -q
```

## Ограничения

- Не переводить политику в `open` без явной причины.
- Не считать allowlist рабочим, если он пустой или содержит мусор вместо sender-идентификаторов.
- После repair всегда читать итоговый runtime-конфиг, а не только stdout скрипта.
