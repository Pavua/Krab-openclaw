#!/bin/zsh
# OpenClaw Ops Guard (диагностика без изменений).
# Зачем: одной кнопкой проверить критичные точки OpenClaw/Krab.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PROFILE="${OPENCLAW_PROFILE_NAME:-main}"

clear
echo "🛡️ OpenClaw Ops Guard (check-only)"
echo "Профиль: ${PROFILE}"
echo "Дата: $(date)"
echo

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/openclaw_ops_guard.py" --profile "${PROFILE}" || true

echo
echo "Готово. Для авто-ремедиации запусти:"
echo "  ./openclaw_prod_harden.command"
echo
read -k 1 -s "?Нажми любую клавишу для выхода..."

