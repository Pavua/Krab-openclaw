# Telegram MCP Stability — 2026-04-03

## Что оказалось реальной проблемой

- Это был не один баг, а смесь из трёх разных слоёв:
  - старые MCP-конфиги указывали на legacy `.venv` и местами на несуществующий путь `mcp-servers/telegram/server.py`;
  - в старом контуре жил старый Pyrogram (`2.0.106` / Python `3.12`), из-за чего в логах были `unknown constructor`, `Peer id invalid` и другие шумные ошибки;
  - после правки конфигов открытая сессия Codex/Claude не hot-reload’ит stdio-MCP, поэтому внутри уже живого чата продолжал всплывать `Transport closed`, хотя direct bridge уже был исправен.

## Что сделано

- Добавлен sync-скрипт:
  [sync_telegram_mcp_configs.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/sync_telegram_mcp_configs.py)
- Добавлена one-click синхронизация:
  [Sync Telegram MCP Configs.command](/Users/pablito/Antigravity_AGENTS/Краб/Sync%20Telegram%20MCP%20Configs.command)
- Sync теперь:
  - переводит Codex и Claude на единый Python:
    `/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python`
  - использует единый wrapper:
    `/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_telegram_mcp_account.py`
  - фиксирует два канонических session-контура:
    - `kraab`
    - `p0lrd_cc`
  - вычищает legacy Telegram MCP entry из Claude, чтобы параллельно не жили старые `.venv`-инстансы

- Добавлен direct smoke без участия MCP-хоста:
  [verify_telegram_mcp_accounts.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/verify_telegram_mcp_accounts.py)
- Добавлена one-click проверка:
  [Verify Telegram MCP Accounts.command](/Users/pablito/Antigravity_AGENTS/Краб/Verify%20Telegram%20MCP%20Accounts.command)

## Что проверено

- Unit:
  - `pytest -q tests/unit/test_sync_telegram_mcp_configs.py tests/unit/test_verify_telegram_mcp_accounts.py`
- Direct smoke обоих аккаунтов через `venv`:
  - `get_dialogs` -> OK
  - `get_chat_history('p0lrd')` -> OK
  - `search('Codex')` -> OK
- Живая отправка напрямую через bridge:
  - `kraab -> @p0lrd` -> OK
  - `p0lrd_cc -> @p0lrd` -> OK

## Практический вывод

- Если direct verify зелёный, а в открытом Codex/Claude всё ещё `Transport closed`, проблема уже не в Telegram bridge и не в session-файлах.
- В этом случае нужен обычный restart клиента, чтобы он перечитал MCP-конфиг и поднял новые stdio-процессы.

## Быстрый runbook

1. Запустить [Sync Telegram MCP Configs.command](/Users/pablito/Antigravity_AGENTS/Краб/Sync%20Telegram%20MCP%20Configs.command)
2. Запустить [Verify Telegram MCP Accounts.command](/Users/pablito/Antigravity_AGENTS/Краб/Verify%20Telegram%20MCP%20Accounts.command)
3. Если verify зелёный, но tools в чате мёртвые:
   - закрыть Codex/Claude
   - открыть заново

## Остаточный риск

- stdio-MCP по своей природе зависит от lifecycle хоста. Если хост убил transport, direct bridge может быть полностью здоров, но текущий чат всё равно увидит только `Transport closed` до следующего reconnect/restart.
