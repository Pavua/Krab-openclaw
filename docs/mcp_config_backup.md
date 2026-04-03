# MCP Config Backup (claude_desktop_config.json → mcpServers)

Последнее обновление: 2026-04-02

Если MCP серверы отвалились после перезапуска Claude Desktop — скопируй это в:
`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "krab-yung-nagato": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/.venv/bin/python",
      "args": [
        "/Users/pablito/Antigravity_AGENTS/Краб/mcp-servers/telegram/server.py",
        "--transport",
        "stdio"
      ]
    },
    "krab-p0lrd": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/.venv/bin/python",
      "args": [
        "/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_telegram_mcp_account.py",
        "--session-name",
        "p0lrd_cc",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

## Описание серверов

| Имя | Аккаунт | Файл |
|-----|---------|------|
| `krab-yung-nagato` | Userbot Yung Nagato (бот Краба) | `mcp-servers/telegram/server.py` |
| `krab-p0lrd` | Личный аккаунт p0lrd (для чтения чатов) | `scripts/run_telegram_mcp_account.py` |

## Session файлы

Хранятся в `~/.krab_mcp_sessions/`. Нужные:
- `p0lrd_cc_mcp.session` — для krab-p0lrd
- `kraab_cc_mcp.session` / `kraab_mcp.session` — для krab-yung-nagato (userbot)

## Быстрое восстановление (Terminal)

```bash
# Просто скопировать актуальный бэкап поверх конфига
cp /Users/pablito/Antigravity_AGENTS/Краб/docs/mcp_config_backup.json \
   ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Или скопировать JSON выше вручную в файл.
После замены — перезапустить Claude Desktop.
