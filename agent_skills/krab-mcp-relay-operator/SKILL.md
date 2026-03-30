---
name: krab-mcp-relay-operator
description: "Поднимать, диагностировать и верифицировать Browser Relay, MCP registry и LM Studio MCP sync для проекта `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно восстановить `attached/authorized/action probe`, синхронизировать `~/.lmstudio/mcp.json`, отладить Browser / MCP Readiness в UI или понять, почему relay/MCP не сходится с runtime truth."
---

# Krab Mcp Relay Operator

Используй этот навык, когда надо довести Browser/MCP-контур до рабочего состояния и доказать это фактом. Главный принцип: `attached` и `authorized` сами по себе недостаточны, нужен успешный action probe.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Проверить managed MCP registry и live `~/.lmstudio/mcp.json`.
2. При расхождении выполнить синхронизацию.
3. Проверить Browser Relay и readiness через UI/CLI.
4. Если UI умеет стартовать relay, подтвердить сценарий браузерным кликом.
5. Проверить конечный action probe, а не только промежуточные флаги.

## Основные точки входа

```bash
python3 scripts/sync_lmstudio_mcp.py
./Sync LM Studio MCP.command
python3 scripts/run_managed_mcp_server.py
pytest tests/unit/test_mcp_registry.py -q
```

## Полезные файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/LM_STUDIO_MCP_SETUP_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/mcp_registry.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`

## Ограничения

- Не путать `401` или отсутствие токена с доказанным падением relay.
- Не завершать работу на статусе `attached=true`, если action probe не подтверждён.
- Если меняешь managed registry, перепроверь live sync после записи.
