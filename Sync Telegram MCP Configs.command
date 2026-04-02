#!/bin/bash
# Назначение: one-click синхронизация Telegram MCP-конфигов Codex и Claude.
# Связи: вызывает scripts/sync_telegram_mcp_configs.py и делает backup перед записью.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON_BIN="$DIR/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "❌ Не найден Python окружения: $PYTHON_BIN"
  read -p "Нажми Enter для закрытия окна..."
  exit 1
fi

echo "🔧 Синхронизирую Telegram MCP конфиги Codex и Claude..."
"$PYTHON_BIN" "$DIR/scripts/sync_telegram_mcp_configs.py"
echo "✅ Синхронизация завершена."
echo "ℹ️ Если Claude/Codex были открыты, лучше перезапустить их для перечитывания конфигов."
read -p "Нажми Enter для закрытия окна..."
