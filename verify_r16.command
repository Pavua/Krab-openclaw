#!/bin/bash
# =============================================================================
# Современная проверка стабильности backend-блока (совместимость с legacy R16).
# Зачем: в старой документации есть ссылка на verify_r16.command; этот файл
# восстанавливает one-click прогон актуального набора backend тестов.
# Связь: запускает текущие unit-тесты runtime/маршрутизации/автосвитча.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

pick_python() {
  local candidates=()
  [[ -x ".venv/bin/python" ]] && candidates+=(".venv/bin/python")
  [[ -x "venv/bin/python" ]] && candidates+=("venv/bin/python")
  command -v python3 >/dev/null 2>&1 && candidates+=("$(command -v python3)")
  command -v python >/dev/null 2>&1 && candidates+=("$(command -v python)")

  local py
  for py in "${candidates[@]}"; do
    if "$py" - <<'PY' >/dev/null 2>&1
import importlib.util as u
raise SystemExit(0 if u.find_spec("pytest") else 1)
PY
    then
      echo "$py"
      return 0
    fi
  done

  # fallback: первый доступный python
  if [[ ${#candidates[@]} -gt 0 ]]; then
    echo "${candidates[0]}"
    return 0
  fi
  return 1
}

PYTHON_BIN="$(pick_python || true)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "❌ Не найден Python."
  exit 2
fi

echo "🚀 Starting backend stability verification (R16 compatibility mode)..."
echo "🐍 Python: $PYTHON_BIN"

"$PYTHON_BIN" -m pytest -q \
  tests/unit/test_openclaw_client.py \
  tests/unit/test_model_manager.py \
  tests/unit/test_openclaw_model_autoswitch.py \
  tests/unit/test_openclaw_runtime_repair.py \
  tests/unit/test_web_app_runtime_endpoints.py

EXIT_CODE=$?
echo
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "✅ Verification completed successfully."
else
  echo "❌ Verification failed with code: $EXIT_CODE"
fi

exit "$EXIT_CODE"
