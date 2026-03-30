---
name: krab-browser-relay-chrome-profile
description: "Диагностировать и восстанавливать Browser Relay, chrome-profile, remote debugging и связанный MCP readiness для проекта `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда browser automation перестаёт видеть живой Chrome-профиль, relay не attach-ится, owner UI показывает неверную Browser / MCP Readiness стадию или нужно восстановить рабочий browser контур после restart/upgrade."
---

# Krab Browser Relay Chrome Profile

Используй этот навык для browser-контура, который опирается на живой Chrome-профиль. Если browser readiness сломан, проверяй не только relay, но и сам профиль, remote debugging и связку с MCP.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Проверить, поднят ли Chrome с нужным профилем и remote debugging.
2. Проверить attach Browser Relay к профилю.
3. Проверить sync MCP и требуемые registry entries.
4. Пройти action probe или UI-driven старт relay.
5. Подтвердить итог через owner panel и/или browser acceptance.

## Полезные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/new Enable Chrome Remote Debugging.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_managed_mcp_server.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/channels_photo_chrome_acceptance.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/Sync LM Studio MCP.command`

## Ограничения

- Не считать attach достаточным без action probe.
- Не смешивать проблемы Chrome-профиля и проблемы MCP в один диагноз без разведения причин.
- После restart Chrome или relay всегда перепроверять readiness заново.
