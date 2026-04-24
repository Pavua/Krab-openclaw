#!/bin/bash
# Self-heal: детектит текущий trycloudflare URL в логе cloudflared
# и обновляет webhook config в Sentry если URL изменился.
#
# Зависит от env:
#   - SENTRY_AUTH_TOKEN (в ~/Antigravity_AGENTS/Краб/.env)
#   - SENTRY_ORG_SLUG=po-zm (по умолчанию)
#   - SENTRY_PROJECTS="python-fastapi krab-ear-agent krab-ear-backend"
#
# Использование:
#   - Ручной: ./cf_tunnel_sync.sh
#   - LaunchAgent: каждые 30 сек поллинг

set -euo pipefail

LOG=/tmp/krab_cf_tunnel/tunnel.log
STATE=/tmp/krab_cf_tunnel/last_url
ENV_FILE="$HOME/Antigravity_AGENTS/Краб/.env"

ORG="${SENTRY_ORG_SLUG:-po-zm}"
PROJECTS="${SENTRY_PROJECTS:-python-fastapi krab-ear-agent krab-ear-backend}"

if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [ -z "${SENTRY_AUTH_TOKEN:-}" ]; then
    echo "[cf_tunnel_sync] ERR: SENTRY_AUTH_TOKEN not set" >&2
    exit 2
fi

# Берём САМЫЙ последний URL из лога (grep по всему файлу, tail -1)
current_url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1)
if [ -z "$current_url" ]; then
    echo "[cf_tunnel_sync] no URL in log yet — exit"
    exit 0
fi

last_url=$(cat "$STATE" 2>/dev/null || echo "")
if [ "$current_url" = "$last_url" ]; then
    # URL не менялся — ничего не делаем
    exit 0
fi

echo "[cf_tunnel_sync] URL changed: $last_url → $current_url"

webhook_url="${current_url}/api/hooks/sentry"

for proj in $PROJECTS; do
    http_status=$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
        "https://sentry.io/api/0/projects/${ORG}/${proj}/plugins/webhooks/" \
        -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"urls\":\"$webhook_url\"}")
    echo "[cf_tunnel_sync]   $proj → $http_status"
done

mkdir -p "$(dirname "$STATE")"
echo "$current_url" > "$STATE"
echo "[cf_tunnel_sync] saved new URL as baseline"
