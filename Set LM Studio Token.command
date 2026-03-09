#!/bin/bash
# Назначение: скрыто записывает LM Studio API token в .env проекта.
# Связи: использует scripts/set_env_secret.py и runtime auth helpers для LM Studio.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/set_env_secret.py LM_STUDIO_API_KEY --label "LM Studio API token"

echo
read -r -p "Нажмите Enter, чтобы закрыть окно..."
