#!/bin/bash
# Poll Sentry для новых unresolved issues и отправляет alert в Telegram.
#
# Замена webhook'а — нужна потому что Sentry блокирует *.trycloudflare.com URLs,
# а реального домена/cert'а пока нет (см. CLAUDE.md → cf_tunnel_sync блок).
#
# Принцип:
#   1. GET /api/0/projects/{org}/{proj}/issues/?query=is:unresolved&statsPeriod={WINDOW}
#   2. Для каждого issue.id, которого нет в STATE_FILE → формат + Telegram send
#   3. Записать новые id в STATE_FILE (rolling window 1000)
#
# Env (из .env):
#   SENTRY_AUTH_TOKEN, SENTRY_ORG_SLUG (default po-zm),
#   SENTRY_PROJECTS (default "python-fastapi krab-ear-agent krab-ear-backend"),
#   OPENCLAW_TELEGRAM_BOT_TOKEN, OWNER_USER_IDS,
#   SENTRY_POLL_WINDOW (default 15m), SENTRY_POLL_LEVELS (default "error fatal")
#
# Запуск: ./sentry_poll_alerts.sh  (LaunchAgent дёргает каждые 5 мин)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KRAB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$KRAB_ROOT/.env"
STATE_DIR="/tmp/krab_sentry_poll"
STATE_FILE="$STATE_DIR/seen_ids"
LOG_FILE="$STATE_DIR/poll.log"
ERR_LOG="$STATE_DIR/poll.err.log"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

log()     { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }
log_err() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERR $*" | tee -a "$ERR_LOG" >&2; }

# Load env
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

ORG="${SENTRY_ORG_SLUG:-po-zm}"
PROJECTS="${SENTRY_PROJECTS:-python-fastapi krab-ear-agent krab-ear-backend}"
WINDOW="${SENTRY_POLL_WINDOW:-24h}"  # API ограничен: '', '24h', '14d'
LEVELS="${SENTRY_POLL_LEVELS:-error fatal}"

if [ -z "${SENTRY_AUTH_TOKEN:-}" ]; then
    log_err "SENTRY_AUTH_TOKEN not set — exit"
    exit 2
fi
TG_TOKEN="${OPENCLAW_TELEGRAM_BOT_TOKEN:-}"
TG_OWNER=$(echo "${OWNER_USER_IDS:-}" | cut -d, -f1)
if [ -z "$TG_TOKEN" ] || [ -z "$TG_OWNER" ]; then
    log_err "Telegram creds missing (token or owner_id) — exit"
    exit 3
fi

# Build level query: "level:[error,fatal]"
LEVEL_QUERY=$(echo "$LEVELS" | tr ' ' ',' | sed 's/^/level:[/' | sed 's/$/]/')
QUERY="is:unresolved ${LEVEL_QUERY}"

send_telegram() {
    local text="$1"
    local issue_url="$2"
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
    'chat_id': '$TG_OWNER',
    'text': sys.argv[1],
    'parse_mode': 'HTML',
    'disable_web_page_preview': True,
    'reply_markup': {'inline_keyboard': [[{'text': '🔗 Open in Sentry', 'url': sys.argv[2]}]]}
}))
" "$text" "$issue_url")

    local status
    status=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
        -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>>"$ERR_LOG" || echo "000")
    if [ "$status" != "200" ]; then
        log_err "telegram_send_failed status=$status"
        return 1
    fi
    return 0
}

format_alert_text() {
    # stdin: single issue JSON
    python3 -c "
import json, sys, html
d = json.load(sys.stdin)
emoji = {'fatal': '🔥', 'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}.get(d.get('level','error'), '⚡')
title = html.escape((d.get('title') or 'Unknown')[:200])
culprit = html.escape((d.get('culprit') or '')[:120])
proj = html.escape(((d.get('project') or {}).get('slug') or '')[:50])
shortid = html.escape(d.get('shortId') or '')
count = d.get('count') or 0
users = d.get('userCount') or 0
last_seen = (d.get('lastSeen') or '')[:19].replace('T', ' ')
lines = [
    f'{emoji} <b>Sentry alert</b> — {d.get(\"level\",\"error\").upper()}',
    f'[{proj}] {title}',
]
if culprit:
    lines.append(f'<i>culprit:</i> {culprit}')
lines.append(f'<i>events:</i> {count} • <i>users:</i> {users} • <i>last:</i> {last_seen}')
lines.append(f'<i>id:</i> <code>{shortid}</code>')
print('\n'.join(lines))
"
}

total_new=0
total_sent=0

for proj in $PROJECTS; do
    log "polling project=$proj"
    # Fetch issues
    response=$(curl -sS --max-time 15 \
        "https://sentry.io/api/0/projects/${ORG}/${proj}/issues/?statsPeriod=${WINDOW}&query=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")&limit=20" \
        -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" 2>>"$ERR_LOG" || echo "[]")

    # Validate JSON array
    if ! echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
        log_err "non-list response project=$proj first120=${response:0:120}"
        continue
    fi

    # Process each issue
    while IFS= read -r issue_json; do
        [ -z "$issue_json" ] && continue
        issue_id=$(echo "$issue_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id') or '')")
        [ -z "$issue_id" ] && continue

        if grep -qFx "$issue_id" "$STATE_FILE"; then
            continue  # already alerted
        fi

        total_new=$((total_new + 1))
        text=$(echo "$issue_json" | format_alert_text)
        permalink=$(echo "$issue_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('permalink') or '')")

        if send_telegram "$text" "$permalink"; then
            echo "$issue_id" >> "$STATE_FILE"
            total_sent=$((total_sent + 1))
            log "alert_sent project=$proj issue_id=$issue_id"
        fi
    done < <(echo "$response" | python3 -c "
import sys, json
for issue in json.load(sys.stdin):
    print(json.dumps(issue))
")
done

# Trim state file to last 1000 entries (FIFO)
if [ "$(wc -l < "$STATE_FILE")" -gt 1000 ]; then
    tail -1000 "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
    log "state_trimmed to 1000"
fi

log "poll_done new=$total_new sent=$total_sent"
exit 0
