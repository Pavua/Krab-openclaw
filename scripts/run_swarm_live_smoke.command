#!/bin/zsh
# Krab: one-click swarm smoke (mock/live) через актуальный AgentRoom + OpenClawClient

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PY=".venv_krab/bin/python"
else
  PY="python3"
fi

MODE="${SWARM_SMOKE_MODE:-mock}"
ROUNDS="${SWARM_SMOKE_ROUNDS:-1}"
FORCE_CLOUD="${SWARM_SMOKE_FORCE_CLOUD:-0}"
REQUIRE_CLOUD_CHANNEL="${SWARM_SMOKE_REQUIRE_CLOUD_CHANNEL:-0}"

EXTRA_ARGS=()
if [[ "$FORCE_CLOUD" == "1" ]]; then
  EXTRA_ARGS+=("--force-cloud")
fi
if [[ "$REQUIRE_CLOUD_CHANNEL" == "1" ]]; then
  EXTRA_ARGS+=("--require-cloud-channel")
fi

echo "🐝 Запускаю swarm live smoke через: $PY"
echo "⚙️ mode=$MODE rounds=$ROUNDS force_cloud=$FORCE_CLOUD require_cloud_channel=$REQUIRE_CLOUD_CHANNEL"
"$PY" scripts/swarm_live_smoke.py --mode "$MODE" --rounds "$ROUNDS" "${EXTRA_ARGS[@]}" || true
echo ""
echo "Подсказка:"
echo "  SWARM_SMOKE_MODE=live SWARM_SMOKE_FORCE_CLOUD=1 SWARM_SMOKE_REQUIRE_CLOUD_CHANNEL=1 ./scripts/run_swarm_live_smoke.command"
echo ""
echo "Нажми любую клавишу для выхода..."
read -k 1
