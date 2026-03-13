#!/bin/bash
# =============================================================================
# One-click живой E2E reserve Telegram Bot.
# Зачем: подтвердить полный owner -> reserve bot -> reply без ручного набора.
# Связь: использует scripts/live_reserve_telegram_roundtrip.py.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
OUT_FILE="$OUT_DIR/reserve_telegram_roundtrip_${STAMP}.json"

"$PYTHON_BIN" scripts/live_reserve_telegram_roundtrip.py --output "$OUT_FILE" "$@"
ROUNDTRIP_CODE=$?

echo
echo "Отчёт: $OUT_FILE"
if [[ "$ROUNDTRIP_CODE" -eq 0 ]]; then
  echo "✅ Reserve round-trip подтверждён. Код выхода: $ROUNDTRIP_CODE"
else
  echo "❌ Reserve round-trip не подтверждён. Код: $ROUNDTRIP_CODE"
fi

exit "$ROUNDTRIP_CODE"
