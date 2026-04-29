#!/bin/bash
# External watchdog для OpenClaw Gateway.
#
# Зачем: launchd KeepAlive=true спасает от crash, но НЕ спасает от полного bootout
# (launchctl bootout / ручного unload). Если сервис выгружен совсем — KeepAlive
# бессилен. Этот watchdog детектит "полное отсутствие" в launchctl list и
# автоматически перезагружает plist, плюс шлёт alert в Telegram Saved Messages.
#
# Запуск: LaunchAgent ai.krab.gateway-watchdog (StartInterval=300 = 5 мин).
# Ручной тест: ./scripts/openclaw_gateway_watchdog.sh

set -u

LABEL="ai.openclaw.gateway"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ENV_FILE="$HOME/Antigravity_AGENTS/Краб/.env"
STATE_DIR="/tmp/krab_gateway_watchdog"
STATE_FILE="${STATE_DIR}/last_state"
LOG_FILE="${STATE_DIR}/watchdog.log"

mkdir -p "$STATE_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

send_telegram_alert() {
    local message="$1"
    # Выцеживаем токен и owner id из .env
    local token owner_id
    token=$(grep -E '^OPENCLAW_TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
    owner_id=$(grep -E '^OWNER_USER_IDS=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | cut -d, -f1)

    if [[ -z "$token" || -z "$owner_id" ]]; then
        log "telegram_alert_skip: no token or owner_id"
        return 1
    fi

    local resp_log="${STATE_DIR}/tg_resp.log"
    local status
    status=$(curl -sS --max-time 10 -o "$resp_log" -w '%{http_code}' \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${owner_id}" \
        -d "text=${message}" \
        -d "parse_mode=HTML" 2>>"$LOG_FILE" || echo "000")
    if [[ "$status" != "200" ]]; then
        log "telegram_alert_failed http=$status (resp: $(head -c 200 "$resp_log" 2>/dev/null || true))"
        return 1
    fi
    log "telegram_alert_sent http=200"
    return 0
}

# Проверяем: зарегистрирован ли LaunchAgent в launchctl list?
# launchctl list выводит "PID STATUS LABEL" или ничего если нет.
if launchctl list "$LABEL" >/dev/null 2>&1; then
    # Сервис жив — всё хорошо.
    echo "ok" > "$STATE_FILE"
    log "gateway_present: OK"
    exit 0
fi

# Сервис отсутствует в launchctl list — полный bootout.
log "gateway_missing: bootout detected, attempting reload"

if [[ ! -f "$PLIST" ]]; then
    log "plist_not_found: $PLIST — cannot reload"
    send_telegram_alert "🚨 <b>Gateway watchdog</b>: plist не найден ($PLIST). Вручную восстановить OpenClaw Gateway."
    exit 1
fi

# launchctl load -w (переопределяет Disabled если был)
if launchctl load -w "$PLIST" 2>>"$LOG_FILE"; then
    load_exit=0
else
    load_exit=$?
fi
if [[ "$load_exit" -eq 0 ]]; then
    log "reload_ok"
    # Даём launchd ~3 сек подняться
    sleep 3
    if launchctl list "$LABEL" >/dev/null 2>&1; then
        send_telegram_alert "🛠️ <b>Gateway watchdog</b>: OpenClaw Gateway был выгружен (bootout) — перезагрузил автоматически. Всё ОК."
        echo "recovered" > "$STATE_FILE"
        exit 0
    else
        send_telegram_alert "🚨 <b>Gateway watchdog</b>: load -w выполнен, но сервис всё ещё отсутствует. Нужен ручной разбор."
        echo "failed" > "$STATE_FILE"
        exit 2
    fi
else
    log "reload_failed exit=$load_exit"
    send_telegram_alert "🚨 <b>Gateway watchdog</b>: не удалось launchctl load -w ${PLIST} (exit=${load_exit}). Fallback: попробуй вручную \`launchctl bootstrap gui/$(id -u) ${PLIST}\`. См. ${LOG_FILE}." || log "fallback_alert_failed"
    echo "failed" > "$STATE_FILE"
    exit 2
fi
