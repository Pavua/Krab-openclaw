#!/bin/zsh
# OpenClaw PROD Harden.
# Зачем: безопасно привести боевой профиль к стабильному baseline.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PROFILE="main"

clear
echo "🛡️ OpenClaw PROD Harden"
echo "Профиль: ${PROFILE}"
echo "Дата: $(date)"
echo

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/openclaw_ops_guard.py" --profile "${PROFILE}" --fix || true

echo
echo "Готово. Рекомендую после этого:"
echo "  1) ./full_restart.command"
echo "  2) !status"
echo "  3) !ops"
echo
read -k 1 -s "?Нажми любую клавишу для выхода..."

