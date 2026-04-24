#!/bin/bash
# ============================================================================
# Auto-Rollback Watchdog для Krab
# ----------------------------------------------------------------------------
# Safety-net для production commits: если за последние WINDOW_MIN минут
# Sentry показал > THRESHOLD новых issues относительно последнего commit,
# откатывает последний commit (git revert HEAD) и шлёт alert в Telegram.
#
# Активация:
#   - В .env:  KRAB_AUTO_ROLLBACK_ENABLED=1
#   - launchctl load ~/Library/LaunchAgents/ai.krab.auto-rollback-watchdog.plist
#
# По умолчанию: DISABLED (exit 0 без действий если env=0 / не задан).
# Escape hatch:
#   - commit message c [skip-autorevert] — не трогаем
#   - touch /tmp/krab_rollback_abort — отмена за 2 мин до revert
# ============================================================================
set -euo pipefail

# ---- Config ----------------------------------------------------------------
THRESHOLD="${KRAB_AUTO_ROLLBACK_THRESHOLD:-10}"
WINDOW_MIN="${KRAB_AUTO_ROLLBACK_WINDOW_MIN:-5}"
MAX_COMMIT_AGE_MIN="${KRAB_AUTO_ROLLBACK_MAX_COMMIT_AGE_MIN:-10}"
ALERT_WAIT_SEC="${KRAB_AUTO_ROLLBACK_ALERT_WAIT_SEC:-120}"
RATE_LIMIT_SEC="${KRAB_AUTO_ROLLBACK_RATE_LIMIT_SEC:-3600}"

REPO="${KRAB_REPO:-$HOME/Antigravity_AGENTS/Краб}"
ENV_FILE="${KRAB_ENV_FILE:-$REPO/.env}"
LOG="${KRAB_AUTO_ROLLBACK_LOG:-/tmp/krab_auto_rollback.log}"
STATE_FILE="${KRAB_AUTO_ROLLBACK_STATE:-/tmp/krab_rollback_last.ts}"
ABORT_FLAG="${KRAB_AUTO_ROLLBACK_ABORT_FLAG:-/tmp/krab_rollback_abort}"

# Sentry
SENTRY_ORG="${SENTRY_ORG:-krab}"
SENTRY_PROJECT="${SENTRY_PROJECT:-krab-userbot}"
SENTRY_API_BASE="${SENTRY_API_BASE:-https://sentry.io/api/0}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# ---- Load .env -------------------------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set +u
    set -a; source "$ENV_FILE"; set +a
    set -u
fi

# ---- Gate: enabled? --------------------------------------------------------
if [[ "${KRAB_AUTO_ROLLBACK_ENABLED:-0}" != "1" ]]; then
    # Выходим тихо — watchdog dormant.
    exit 0
fi

log "=== check start (threshold=$THRESHOLD, window=${WINDOW_MIN}m) ==="

cd "$REPO"

# ---- Rate limit (не чаще чем раз в час) ------------------------------------
if [[ -f "$STATE_FILE" ]]; then
    last_ts=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    now_ts=$(date +%s)
    delta=$((now_ts - last_ts))
    if [[ $delta -lt $RATE_LIMIT_SEC ]]; then
        log "rate-limit: last revert ${delta}s ago (< ${RATE_LIMIT_SEC}s), skip"
        exit 0
    fi
fi

# ---- Проверка последнего commit --------------------------------------------
last_sha=$(git rev-parse HEAD)
last_msg=$(git log -1 --pretty=%B HEAD)
last_age_sec=$(($(date +%s) - $(git log -1 --pretty=%ct HEAD)))
last_age_min=$((last_age_sec / 60))

log "last commit: $last_sha (age ${last_age_min}m)"

# Guard 1: старый commit (прошло окно опасности)
if [[ $last_age_min -gt $MAX_COMMIT_AGE_MIN ]]; then
    log "commit too old (${last_age_min}m > ${MAX_COMMIT_AGE_MIN}m), skip"
    exit 0
fi

# Guard 2: merge commit (>1 parent)
parents=$(git log -1 --pretty=%P HEAD | wc -w | tr -d ' ')
if [[ $parents -gt 1 ]]; then
    log "merge commit (parents=$parents), skip"
    exit 0
fi

# Guard 3: escape hatch в commit message
if echo "$last_msg" | grep -qF "[skip-autorevert]"; then
    log "commit has [skip-autorevert], skip"
    exit 0
fi

# ---- Sentry check ----------------------------------------------------------
if [[ -z "${SENTRY_AUTH_TOKEN:-}" ]]; then
    log "SENTRY_AUTH_TOKEN not set, skip"
    exit 0
fi

SENTRY_URL="${KRAB_AUTO_ROLLBACK_SENTRY_URL:-$SENTRY_API_BASE/projects/$SENTRY_ORG/$SENTRY_PROJECT/issues/}"

# Sentry API: issues with statsPeriod=Nm & query=is:unresolved age:-Nm
query="is:unresolved age:-${WINDOW_MIN}m"
resp=$(curl -sS -G \
    -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
    --data-urlencode "query=$query" \
    --data-urlencode "statsPeriod=${WINDOW_MIN}m" \
    --data-urlencode "limit=100" \
    "$SENTRY_URL" 2>&1) || {
    log "sentry curl failed: $resp"
    exit 0
}

# count = array length
count=$(echo "$resp" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(len(d) if isinstance(d,list) else 0)
except Exception:
    print(0)' 2>/dev/null || echo 0)

log "sentry new issues in last ${WINDOW_MIN}m: $count (threshold=$THRESHOLD)"

if [[ $count -le $THRESHOLD ]]; then
    log "under threshold, no action"
    exit 0
fi

# ---- SPIKE DETECTED --------------------------------------------------------
log "!!! SPIKE DETECTED: $count new issues > $THRESHOLD — alerting owner"

send_tg() {
    local msg="$1"
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_OWNER_CHAT_ID:-}" ]]; then
        curl -sS -X POST \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_OWNER_CHAT_ID}" \
            --data-urlencode "text=$msg" \
            --data-urlencode "parse_mode=Markdown" > /dev/null || log "tg send failed"
    else
        log "TG creds missing, alert only in log: $msg"
    fi
}

short_sha="${last_sha:0:8}"
subject=$(echo "$last_msg" | head -n1)
rm -f "$ABORT_FLAG"

send_tg "⚠️ *Krab auto-rollback WARNING*
Spike: *$count* new Sentry issues in ${WINDOW_MIN}m (threshold $THRESHOLD).
Commit: \`$short_sha\` — $subject
Revert через ${ALERT_WAIT_SEC}s. Отмена: \`touch $ABORT_FLAG\`"

log "waiting ${ALERT_WAIT_SEC}s for owner intervention..."
sleep "$ALERT_WAIT_SEC"

if [[ -f "$ABORT_FLAG" ]]; then
    log "abort flag found — revert CANCELLED"
    send_tg "✋ *Krab auto-rollback CANCELLED* (abort flag)"
    rm -f "$ABORT_FLAG"
    exit 0
fi

# ---- Revert ----------------------------------------------------------------
log "executing git revert HEAD..."
if git revert HEAD --no-edit >> "$LOG" 2>&1; then
    log "revert OK"
    new_sha=$(git rev-parse HEAD)
    branch=$(git rev-parse --abbrev-ref HEAD)

    # Push (не force)
    if git push origin "$branch" >> "$LOG" 2>&1; then
        log "push OK to origin/$branch"
        send_tg "🔄 *Krab auto-rollback EXECUTED*
Reverted: \`$short_sha\` — $subject
New HEAD: \`${new_sha:0:8}\` on \`$branch\`
Sentry spike: $count issues / ${WINDOW_MIN}m"
    else
        log "push FAILED"
        send_tg "❌ *Krab auto-rollback: push failed*
Local revert committed (\`${new_sha:0:8}\`), но push не прошёл. Проверь вручную."
    fi

    date +%s > "$STATE_FILE"
else
    log "revert FAILED"
    send_tg "❌ *Krab auto-rollback FAILED*
git revert не прошёл для \`$short_sha\`. Нужно вручную."
fi

log "=== check end ==="
