#!/bin/bash
# =============================================================================
# One-click live E2E экосистемы Krab/OpenClaw/Voice/Ear.
# Зачем: дать owner-у воспроизводимый запуск без ручного выбора интерпретатора.
# Связь: использует scripts/live_ecosystem_e2e.py и сохраняет отчёт в artifacts/ops.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PYTHON_BIN=".venv_krab/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "❌ Не найден Python (python3/python)."
  exit 2
fi

"$PYTHON_BIN" scripts/live_ecosystem_e2e.py "$@"
EXIT_CODE=$?

echo
echo "Готово. Код выхода: $EXIT_CODE"
read -r -p "Нажми Enter для закрытия окна..."
exit "$EXIT_CODE"
