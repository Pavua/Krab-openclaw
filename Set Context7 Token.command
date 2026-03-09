#!/bin/bash
# Назначение: скрыто записывает Context7 API token в .env проекта.
# Связи: использует scripts/set_env_secret.py и managed MCP registry.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/set_env_secret.py CONTEXT7_API_KEY --label "Context7 API token"

echo
read -r -p "Нажмите Enter, чтобы закрыть окно..."
