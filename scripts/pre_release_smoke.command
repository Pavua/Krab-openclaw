#!/bin/zsh
# Krab: one-click pre-release smoke (gate + advisory диагностика)

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PY=".venv_krab/bin/python"
else
  PY="python3"
fi

echo "🧪 Запускаю pre-release smoke через: $PY"
if "$PY" scripts/pre_release_smoke.py "$@"; then
  EXIT_CODE=0
else
  EXIT_CODE=$?
fi
echo ""
if [[ "$EXIT_CODE" -eq 2 ]]; then
  echo "⏸ Smoke заблокирован средой, а не регрессией кода."
fi
echo "Примеры:"
echo "  ./scripts/pre_release_smoke.command"
echo "  ./scripts/pre_release_smoke.command --strict-runtime"
echo "  ./scripts/pre_release_smoke.command --full --strict-runtime"
echo ""
echo "Нажми любую клавишу для выхода..."
read -k 1
exit "$EXIT_CODE"
