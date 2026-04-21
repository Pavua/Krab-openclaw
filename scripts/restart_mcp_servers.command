#!/usr/bin/env bash
# Restart 3 Krab MCP servers (yung-nagato / p0lrd / hammerspoon)
# Usage: ./scripts/restart_mcp_servers.command
#
# Диагностика: "Transport closed" обычно вызвана:
#   1. sqlite3.OperationalError: database is locked  — другой процесс держит .session файл
#   2. Процесс упал (OOM при embedding, FloodWait)
#   3. SSE timeout без keep-alive (клиент Claude Desktop потерял соединение)
#
# Hammerspoon (8013) загружается отдельно — plist должен быть в ~/Library/LaunchAgents/

set -euo pipefail

echo "=== Krab MCP restart $(date) ==="

LAUNCHAGENTS_DIR="$(dirname "$0")/launchagents"
LAUNCH_AGENTS_HOME="$HOME/Library/LaunchAgents"

# --- Установка plist hammerspoon если не загружен ---
HS_LABEL="com.krab.mcp-hammerspoon"
HS_PLIST="$LAUNCH_AGENTS_HOME/${HS_LABEL}.plist"
if ! launchctl list "$HS_LABEL" &>/dev/null; then
    echo "hammerspoon: не загружен, устанавливаем plist..."
    if [ -f "$LAUNCHAGENTS_DIR/${HS_LABEL}.plist" ]; then
        cp "$LAUNCHAGENTS_DIR/${HS_LABEL}.plist" "$LAUNCH_AGENTS_HOME/"
        launchctl load "$HS_PLIST" && echo "hammerspoon: plist загружен"
    else
        echo "WARN: $LAUNCHAGENTS_DIR/${HS_LABEL}.plist не найден — пропускаем 8013"
    fi
fi

# --- Kickstart 3 MCP серверов ---
for label in com.krab.mcp-yung-nagato com.krab.mcp-p0lrd com.krab.mcp-hammerspoon; do
    if launchctl list "$label" &>/dev/null 2>&1; then
        launchctl kickstart -k "gui/$(id -u)/$label" 2>&1 && echo "restarted: $label" || echo "WARN: kickstart failed for $label"
    else
        echo "SKIP: $label не загружен в launchd"
    fi
done

echo ""
echo "Ожидание запуска серверов (5 сек)..."
sleep 5

# --- Проверка портов ---
for port in 8011 8012 8013; do
    resp=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:$port/sse" 2>/dev/null || echo "000")
    if [ "$resp" = "200" ] || [ "$resp" = "000" ]; then
        # 000 = connection refused (порт не слушает), 200 = sse открылся
        if lsof -iTCP:"$port" -sTCP:LISTEN &>/dev/null 2>&1; then
            echo "up:   $port (LISTEN)"
        else
            echo "DOWN: $port (не слушает)"
        fi
    else
        echo "up:   $port (HTTP $resp)"
    fi
done

echo ""
echo "После рестарта: переподключите Claude Desktop (выйдите из настроек MCP и зайдите снова)"
echo "Логи: tail -f /tmp/krab-mcp-yung-nagato.err.log"
echo "      tail -f /tmp/krab-mcp-p0lrd.err.log"
echo "      tail -f /tmp/krab-mcp-hammerspoon.err.log"
