#!/bin/bash
# Однокликовый аудит рисков текущего Krab runtime.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x "venv/bin/python" ]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🦀 Krab Runtime Risk Audit"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo
echo "Проверяю panel :8080, gateway :18789, процессы, .env, logs и data/sessions."
echo "По умолчанию remediation не применяется. Для preview: --plan-remediation, для применения: --apply-remediation."
echo

"$PYTHON_BIN" scripts/krab_runtime_risk_audit.py "$@"
EXIT_CODE=$?

echo
echo "Отчёт: artifacts/ops/krab_runtime_risk_audit_latest.json"
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
