#!/bin/bash
# Генерирует единый ops bundle: ops-report + health snapshot.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

OUT_DIR="$ROOT_DIR/artifacts/ops"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"

OPS_FILE="$OUT_DIR/ops_report_bundle_${STAMP}.json"
HEALTH_FILE="$OUT_DIR/health_snapshot_${STAMP}.json"

echo "🧾 Генерирую ops report..."
"$PYTHON_BIN" - <<'PY' > "$OPS_FILE"
import json
import os
from pathlib import Path
from src.core.model_manager import ModelRouter

repo = Path(os.getcwd())
router = ModelRouter(
    config={
        "MODEL_ROUTING_MEMORY_PATH": str(repo / "artifacts" / "model_routing_memory.json"),
        "MODEL_USAGE_REPORT_PATH": str(repo / "artifacts" / "model_usage_report.json"),
        "MODEL_OPS_STATE_PATH": str(repo / "artifacts" / "model_ops_state.json"),
        "CLOUD_SOFT_CAP_CALLS": os.getenv("CLOUD_SOFT_CAP_CALLS", "300"),
        "CLOUD_COST_PER_CALL_USD": os.getenv("CLOUD_COST_PER_CALL_USD", "0.01"),
        "LOCAL_COST_PER_CALL_USD": os.getenv("LOCAL_COST_PER_CALL_USD", "0.0"),
        "CLOUD_MONTHLY_BUDGET_USD": os.getenv("CLOUD_MONTHLY_BUDGET_USD", "25"),
        "MONTHLY_CALLS_FORECAST": os.getenv("MONTHLY_CALLS_FORECAST", "5000"),
    }
)
report = router.get_ops_report(history_limit=100)
print(json.dumps(report, ensure_ascii=False, indent=2))
PY

echo "❤️ Генерирую health snapshot..."
"$PYTHON_BIN" scripts/health_dashboard.py > "$HEALTH_FILE"

echo "✅ Ops bundle готов:"
echo " - $OPS_FILE"
echo " - $HEALTH_FILE"
read -p "Нажми Enter, чтобы закрыть окно..."
