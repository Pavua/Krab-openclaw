# MCP SSE — Troubleshooting "Transport closed"

## Порты

| Сервис | Label | Порт | Лог |
|--------|-------|------|-----|
| yung-nagato (kraab) | `com.krab.mcp-yung-nagato` | 8011 | `/tmp/krab-mcp-yung-nagato.err.log` |
| p0lrd (owner) | `com.krab.mcp-p0lrd` | 8012 | `/tmp/krab-mcp-p0lrd.err.log` |
| hammerspoon | `com.krab.mcp-hammerspoon` | 8013 | `/tmp/krab-mcp-hammerspoon.err.log` |

## Частые причины "Transport closed"

### 1. sqlite3.OperationalError: database is locked
**Симптом в логе:** `sqlite3.OperationalError: database is locked` в lifespan  
**Причина:** Pyrogram `.session` файл заблокирован другим процессом (зомби-инстанс, Krab userbot)  
**Фикс:** kickstart сервера освободит lock. Если не помогает — найти и убить процесс:
```bash
lsof ~/.openclaw/sessions/kraab.session 2>/dev/null
```

### 2. Процесс упал (OOM / FloodWait)
**Симптом:** порт перестал слушать, `launchctl list` показывает ненулевой exit code  
**Проверка:**
```bash
launchctl list | grep krab.mcp
lsof -iTCP -sTCP:LISTEN | grep -E "8011|8012|8013"
```
**Фикс:** kickstart (launchd автоматически перезапустит через ThrottleInterval=5s)

### 3. SSE timeout (Claude Desktop потерял соединение)
**Симптом:** порт слушает, но Claude Desktop жалуется на Transport closed  
**Причина:** SSE соединение живёт долго без активности, некоторые клиенты дропают  
**Фикс:** переподключить MCP в Claude Desktop Settings → Developers → переключить сервер off/on

### 4. hammerspoon MCP не загружен в launchd
**Симптом:** `launchctl list | grep hammerspoon` — пусто  
**Причина:** plist не установлен в `~/Library/LaunchAgents/`  
**Фикс:** скрипт `restart_mcp_servers.command` автоматически установит plist

## Диагностика

```bash
# Состояние launchd
launchctl list | grep krab.mcp

# Проверка портов
lsof -iTCP -sTCP:LISTEN | grep -E "8011|8012|8013"

# SSE endpoint (ожидать 200 OK — соединение откроется)
curl -N http://127.0.0.1:8011/sse  # Ctrl+C после первых строк

# Последние ошибки
tail -30 /tmp/krab-mcp-yung-nagato.err.log
tail -30 /tmp/krab-mcp-p0lrd.err.log
tail -30 /tmp/krab-mcp-hammerspoon.err.log
```

> **Примечание:** `/health` endpoint не реализован в MCP серверах — `curl /health` вернёт 404.
> Для проверки живости используй `lsof -iTCP:PORT -sTCP:LISTEN`.

## Быстрый ремонт

```bash
# Вариант 1: скрипт (рекомендуется)
./scripts/restart_mcp_servers.command

# Вариант 2: вручную
for label in com.krab.mcp-yung-nagato com.krab.mcp-p0lrd com.krab.mcp-hammerspoon; do
    launchctl kickstart -k "gui/$(id -u)/$label"
done
sleep 5
lsof -iTCP -sTCP:LISTEN | grep -E "8011|8012|8013"
```

После рестарта серверов — **переподключить Claude Desktop**: Settings → Developers → MCP сервер off → on (или перезапустить Claude Desktop).

## Известные проблемы (21.04.2026)

- `hammerspoon` plist не был загружен в `~/Library/LaunchAgents/` — порт 8013 не слушал
- `hammerspoon` сервер использует **stdio** транспорт (`mcp.run()` без аргументов), не SSE.
  Plist с `MCP_TRANSPORT=sse` не имеет эффекта — сервер запускается как subprocess Claude Desktop,
  а не как постоянный SSE-сервер. Порт 8013 слушать не будет — это нормально для hammerspoon.
- `yung-nagato` периодически падает с `database is locked` при параллельном старте с userbot
- `/health` HTTP endpoint не реализован ни в одном MCP сервере — это нормально
- Telegram MCP серверы (8011, 8012) имеют SSE транспорт через `run_telegram_mcp_account.py --transport sse`
