#!/bin/zsh
# Krab: one-click swarm smoke (mock)

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PY=".venv_krab/bin/python"
else
  PY="python3"
fi

echo "🐝 Запускаю swarm smoke через: $PY"
"$PY" scripts/swarm_test_script.py || true
echo ""
echo "Нажми любую клавишу для выхода..."
read -k 1

