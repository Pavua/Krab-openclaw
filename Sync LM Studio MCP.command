#!/bin/bash
# Назначение: one-click синхронизация curated MCP-конфига проекта в LM Studio.
# Связи: использует scripts/sync_lmstudio_mcp.py и общий реестр src/core/mcp_registry.py.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🧩 Syncing LM Studio MCP config..."

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/sync_lmstudio_mcp.py --write --backup

echo
echo "✅ LM Studio mcp.json обновлён."
echo "ℹ️ Если LM Studio уже открыт, перезапусти его, чтобы MCP перечитал конфиг."
echo
read -r -p "Нажмите Enter, чтобы закрыть окно..."
