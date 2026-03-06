#!/bin/zsh
# Krab: one-click runtime snapshot (восстановлено из pre-refactor flow)

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PY=".venv_krab/bin/python"
else
  PY="python3"
fi

echo "📸 Запускаю runtime snapshot через: $PY"
"$PY" scripts/runtime_snapshot.py || true
echo ""
echo "Нажми любую клавишу для выхода..."
read -k 1

