#!/bin/bash
# Назначение: one-click проверка двух канонических Telegram MCP аккаунтов.
# Связи: запускает прямой smoke-check через scripts/verify_telegram_mcp_accounts.py
# и не зависит от того, перечитал ли Codex/Claude свои MCP-конфиги.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON_BIN="$DIR/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "❌ Не найден Python окружения: $PYTHON_BIN"
  read -p "Нажми Enter для закрытия окна..."
  exit 1
fi

echo "🧪 Проверяю Telegram MCP аккаунты напрямую..."
"$PYTHON_BIN" "$DIR/scripts/verify_telegram_mcp_accounts.py"
echo "✅ Проверка завершена."
read -p "Нажми Enter для закрытия окна..."
