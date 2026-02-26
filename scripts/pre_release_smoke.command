#!/bin/zsh
# Единый pre-release smoke script для ключевых проверок.
# Вызывает Python скрипт pre_release_smoke.py со всеми необходимыми проверками.
# Использование: ./scripts/pre_release_smoke.command [--full] [--strict-runtime]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "🚀 Запуск единого Pre-Release Smoke скрипта..."

VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
if [[ -f "$VENV_PYTHON" ]]; then
    PYTHON_BIN="$VENV_PYTHON"
else
    PYTHON_BIN="python3"
fi

if [[ -f "${ROOT_DIR}/scripts/pre_release_smoke.py" ]]; then
    exec "$PYTHON_BIN" "${ROOT_DIR}/scripts/pre_release_smoke.py" "$@"
else
    echo "❌ Ошибка: scripts/pre_release_smoke.py не найден."
    exit 1
fi
