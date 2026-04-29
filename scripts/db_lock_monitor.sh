#!/usr/bin/env bash
# db_lock_monitor.sh — observability для archive.db SQLite lock-регрессий.
#
# Раз в час сканирует логи Krab за последние 60 минут, считает упоминания
# "database is locked" по timestamps. Если >THRESHOLD/час — отправляет alert
# в Telegram (с cooldown 6 часов между алертами одинакового severity).
#
# Также фиксирует baseline pragmas archive.db (busy_timeout, journal_mode),
# чтобы при будущей регрессии было видно — pragma была изменена или нет.
#
# Exit codes:
#   0 = OK / under threshold
#   1 = threshold exceeded (alert dispatched или suppressed cooldown'ом)
#   2 = config error (нет токена, нет лога, нет sqlite3)
#
# Контракт env (читается из .env через grep — без source чтобы не словить shell-quote баги):
#   OPENCLAW_TELEGRAM_BOT_TOKEN — токен бота для уведомлений
#   OWNER_USER_IDS              — chat_id (берётся первый из CSV)
#   DB_LOCK_THRESHOLD           — override threshold (default 5)

set -uo pipefail

KRAB_ROOT="/Users/pablito/Antigravity_AGENTS/Краб"
LOG_FILE="${KRAB_ROOT}/logs/krab_launchd.out.log"
ENV_FILE="${KRAB_ROOT}/.env"
DB_FILE="${HOME}/.openclaw/krab_memory/archive.db"

STATE_DIR="/tmp/krab_db_lock_monitor"
LAST_ALERT_FILE="${STATE_DIR}/last_alert_ts"
BASELINE_FILE="${STATE_DIR}/pragma_baseline"
RUN_LOG="${STATE_DIR}/run.log"

mkdir -p "${STATE_DIR}"

# --- helpers ---------------------------------------------------------------

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '[%s] %s\n' "${ts}" "$*" | tee -a "${RUN_LOG}" >&2
}

read_env_var() {
    # читаем "KEY=value" из .env, режем кавычки и комментарии
    local key="$1"
    [[ -f "${ENV_FILE}" ]] || return 1
    grep -E "^${key}=" "${ENV_FILE}" | tail -1 | cut -d'=' -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

send_telegram() {
    local message="$1"
    local token chat_id_raw chat_id
    token="$(read_env_var OPENCLAW_TELEGRAM_BOT_TOKEN || true)"
    chat_id_raw="$(read_env_var OWNER_USER_IDS || true)"
    chat_id="${chat_id_raw%%,*}"  # первый id из CSV

    if [[ -z "${token}" || -z "${chat_id}" ]]; then
        log "ERROR: missing telegram credentials in .env"
        return 1
    fi

    curl -sS -X POST \
        "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${message}" \
        --data-urlencode "parse_mode=HTML" \
        -o /dev/null -w '%{http_code}\n' 2>>"${RUN_LOG}"
}

write_pragma_baseline() {
    # Snapshot текущих pragmas → baseline file. Записывается каждый run чтобы
    # отслеживать дрейф конфигурации (pragma reset → регрессия busy_timeout).
    if ! command -v sqlite3 >/dev/null 2>&1; then
        log "WARN: sqlite3 not found, skipping pragma baseline"
        return 0
    fi
    if [[ ! -f "${DB_FILE}" ]]; then
        log "WARN: archive.db not found at ${DB_FILE}"
        return 0
    fi
    local busy_timeout journal_mode
    busy_timeout="$(sqlite3 "${DB_FILE}" 'PRAGMA busy_timeout;' 2>/dev/null || echo 'ERR')"
    journal_mode="$(sqlite3 "${DB_FILE}" 'PRAGMA journal_mode;' 2>/dev/null || echo 'ERR')"
    {
        printf 'ts=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
        printf 'busy_timeout=%s\n' "${busy_timeout}"
        printf 'journal_mode=%s\n' "${journal_mode}"
    } > "${BASELINE_FILE}"
}

# --- main ------------------------------------------------------------------

THRESHOLD="${DB_LOCK_THRESHOLD:-5}"
COOLDOWN_SEC=$((6 * 3600))  # 6 часов

if [[ ! -f "${LOG_FILE}" ]]; then
    log "ERROR: log file not found: ${LOG_FILE}"
    exit 2
fi

# Sliding window: 60 мин назад от now. Фильтруем по подстроке timestamp в начале строки.
# Логи Krab имеют timestamps формата "2026-04-22 18:42:31" — лексикографически сортируются.
# /bin/date — BSD-вариант, гарантированно есть в macOS (для портабильности
# при минимальном PATH в LaunchAgent). GNU coreutils date в PATH использует
# другой синтаксис ('-d "60 min ago"'), поэтому пробуем оба.
if WINDOW_START="$(/bin/date -v-60M '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"; then
    :
else
    WINDOW_START="$(date -d '60 minutes ago' '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
fi

if [[ -z "${WINDOW_START}" ]]; then
    log "ERROR: cannot compute window start (date utility missing both BSD and GNU syntax)"
    exit 2
fi

COUNT="$(grep -E "database is locked|OperationalError.*lock" "${LOG_FILE}" \
    | grep -oE '2026-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}' \
    | tr 'T' ' ' \
    | awk -v start="${WINDOW_START}" '$0 >= start' \
    | wc -l | tr -d ' ')"

write_pragma_baseline

log "scan: window_start=${WINDOW_START} count=${COUNT} threshold=${THRESHOLD}"

if (( COUNT <= THRESHOLD )); then
    log "OK: under threshold"
    exit 0
fi

# Threshold exceeded — проверяем cooldown
NOW_EPOCH="$(date +%s)"
LAST_ALERT_EPOCH=0
if [[ -f "${LAST_ALERT_FILE}" ]]; then
    LAST_ALERT_EPOCH="$(cat "${LAST_ALERT_FILE}" 2>/dev/null || echo 0)"
fi

ELAPSED=$((NOW_EPOCH - LAST_ALERT_EPOCH))
if (( ELAPSED < COOLDOWN_SEC )); then
    REMAIN=$((COOLDOWN_SEC - ELAPSED))
    log "ALERT SUPPRESSED (cooldown): count=${COUNT} remain=${REMAIN}s"
    exit 1
fi

# Загружаем baseline pragma для контекста алерта
PRAGMA_LINE=""
if [[ -f "${BASELINE_FILE}" ]]; then
    BT="$(grep '^busy_timeout=' "${BASELINE_FILE}" | cut -d'=' -f2)"
    JM="$(grep '^journal_mode=' "${BASELINE_FILE}" | cut -d'=' -f2)"
    PRAGMA_LINE=$'\n'"pragmas: busy_timeout=${BT} journal_mode=${JM}"
fi

MSG="🔒 DB lock spike: ${COUNT} events/hour (threshold ${THRESHOLD}) — last 60min${PRAGMA_LINE}"

if send_telegram "${MSG}"; then
    echo "${NOW_EPOCH}" > "${LAST_ALERT_FILE}"
    log "ALERT SENT: ${MSG}"
else
    log "ALERT FAILED to send"
fi

exit 1
