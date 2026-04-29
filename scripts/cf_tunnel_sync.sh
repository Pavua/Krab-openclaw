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
FAIL_COUNTER=/tmp/krab_cf_tunnel/fail_count
ERR_LOG=/tmp/krab_cf_tunnel/sync.err.log
ENV_FILE="$HOME/Antigravity_AGENTS/Краб/.env"

ORG="${SENTRY_ORG_SLUG:-po-zm}"
PROJECTS="${SENTRY_PROJECTS:-python-fastapi krab-ear-agent krab-ear-backend}"

mkdir -p "$(dirname "$STATE")"

log_err() {
    # stderr + persistent err log
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [cf_tunnel_sync] $*"
    echo "$msg" >&2
    echo "$msg" >> "$ERR_LOG"
}

send_telegram_alert() {
    # Отправка алерта в Telegram Saved Messages (owner).
    local message="$1"
    local token owner_id
    token=$(grep -E '^OPENCLAW_TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
    owner_id=$(grep -E '^OWNER_USER_IDS=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | cut -d, -f1)
    if [[ -z "${token:-}" || -z "${owner_id:-}" ]]; then
        log_err "telegram_alert_skip: no token or owner_id"
        return 1
    fi
    local status
    status=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${owner_id}" \
        -d "text=${message}" \
        -d "parse_mode=HTML" 2>>"$ERR_LOG" || echo "000")
    if [[ "$status" != "200" ]]; then
        log_err "telegram_alert_failed http=$status"
        return 1
    fi
    return 0
}

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

all_ok=1
for proj in $PROJECTS; do
    http_status=$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
        "https://sentry.io/api/0/projects/${ORG}/${proj}/plugins/webhooks/" \
        -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"urls\":\"$webhook_url\"}")
    echo "[cf_tunnel_sync]   $proj → $http_status"
    if [[ "$http_status" != "200" && "$http_status" != "204" ]]; then
        all_ok=0
        log_err "sentry_put_failed proj=$proj http=$http_status"
    fi
done

if [[ "$all_ok" -ne 1 ]]; then
    # НЕ обновляем STATE — чтобы при следующем запуске повторить попытку.
    fails=$(cat "$FAIL_COUNTER" 2>/dev/null || echo "0")
    fails=$((fails + 1))
    echo "$fails" > "$FAIL_COUNTER"
    log_err "skip_state_update (consecutive_fails=$fails)"
    if [[ "$fails" -ge 3 ]]; then
        send_telegram_alert "🚨 <b>cf_tunnel_sync</b>: ${fails} подряд неудачных PUT в Sentry. webhook_url=${webhook_url}. См. ${ERR_LOG}." || true
    fi
    exit 1
fi

# Все PUT прошли успешно — сбрасываем счётчик и обновляем STATE.
echo "0" > "$FAIL_COUNTER"
echo "$current_url" > "$STATE"
echo "[cf_tunnel_sync] saved new URL as baseline"
