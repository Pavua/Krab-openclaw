#!/bin/bash
# =============================================================================
# One-click smoke-проверка каналов и критичных паттернов в логах.
# Зачем: совместимость с runbook (./scripts/live_channel_smoke.command)
# и быстрый запуск smoke без ручного ввода длинной команды.
# Связь: использует scripts/live_channel_smoke.py.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "❌ Не найден Python (python3/python)."
  exit 2
fi

OUT_DIR="artifacts/live_smoke"
mkdir -p "$OUT_DIR"
STAMP="$(date +"%Y%m%d_%H%M%S")"
OUT_FILE="$OUT_DIR/live_channel_smoke_${STAMP}.json"

"$PYTHON_BIN" scripts/live_channel_smoke.py --output "$OUT_FILE" "$@"
SMOKE_CODE=$?

echo
echo "Отчёт: $OUT_FILE"
if [[ "$SMOKE_CODE" -eq 0 ]]; then
  echo "✅ Готово. Код выхода: $SMOKE_CODE"
else
  echo "❌ Smoke завершился с ошибкой. Код: $SMOKE_CODE"
fi

exit "$SMOKE_CODE"
