#!/usr/bin/env zsh
# Start Claude Proxy Server.command
# Запускает локальный OpenAI-совместимый прокси для claude.ai (Claude Pro)
# Порт: 17191

set -uo pipefail

REPO_DIR="/Users/pablito/Antigravity_AGENTS/Краб"
VENV="$REPO_DIR/../Краб/.venv_krab"

if [[ ! -d "$VENV" ]]; then
  VENV="/Users/Shared/Antigravity_AGENTS/Краб/.venv_krab"
fi

cd "$REPO_DIR" || { echo "Repo not found: $REPO_DIR"; exit 1; }

echo "=== Claude Proxy Server ==="
echo "Порт: 17191"
echo "Config: ~/.openclaw/claude_proxy_config.json"
echo ""

if ! "$VENV/bin/python" scripts/claude_proxy_server.py --check 2>/dev/null; then
  echo "⚠️  Session key не настроен или устарел."
  echo ""
  echo "Как получить session key:"
  echo "  1. Откройте claude.ai в Safari/Chrome"
  echo "  2. DevTools (⌥⌘I) → Application → Cookies → https://claude.ai"
  echo "  3. Скопируйте значение cookie 'sessionKey'"
  echo "  4. Выполните:"
  echo "     python scripts/claude_proxy_server.py --set-session sk-ant-sid01-..."
  echo ""
  read -r "?Нажмите Enter для выхода..."
  exit 1
fi

exec "$VENV/bin/python" scripts/claude_proxy_server.py --port 17191
