#!/usr/bin/env bash
# verify_observability.sh — быстрый health-check observability pipeline Краба.
# Проверяет: /metrics новые counters, Prometheus scrape UP, Grafana up,
# dashboard доступен.
# Использование: bash scripts/verify_observability.sh

set -u

OWNER_PANEL="${OWNER_PANEL:-http://127.0.0.1:8080}"
PROM="${PROM:-http://127.0.0.1:9090}"
GRAF="${GRAF:-http://127.0.0.1:3000}"
GRAF_AUTH="${GRAF_AUTH:-admin:krab_local}"

pass() { printf "  [OK] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1"; FAIL=1; }

FAIL=0

echo "== Krab observability verification =="

echo "-- 1. Owner panel /metrics krab_* counters"
curl -s "$OWNER_PANEL/api/memory/search?q=verify_obs" -o /dev/null
sleep 1
MET=$(curl -s "$OWNER_PANEL/metrics")
if echo "$MET" | grep -q "^krab_memory_retrieval_mode_total"; then
    pass "krab_memory_retrieval_mode_total present"
else
    fail "krab_memory_retrieval_mode_total missing"
fi
CNT=$(echo "$MET" | grep -cE "^krab_")
echo "     krab_* lines total: $CNT"

echo "-- 2. Prometheus target scrape health"
HEALTH=$(curl -sf "$PROM/api/v1/targets" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); t=d['data']['activeTargets'][0]; print(t['health'])" 2>/dev/null)
if [ "$HEALTH" = "up" ]; then
    pass "krab_owner_panel target UP"
else
    fail "krab_owner_panel target: $HEALTH"
fi

echo "-- 3. Grafana health"
if curl -sf "$GRAF/api/health" >/dev/null; then
    pass "Grafana API /health OK"
else
    fail "Grafana health check failed"
fi

echo "-- 4. Grafana dashboard krab-main"
if curl -sf -u "$GRAF_AUTH" "$GRAF/api/dashboards/uid/krab-main" >/dev/null; then
    pass "dashboard krab-main imported"
else
    fail "dashboard krab-main not found (import manually)"
fi

echo
if [ "$FAIL" = "0" ]; then
    echo "== All checks passed =="
    exit 0
else
    echo "== Some checks FAILED =="
    exit 1
fi
