#!/bin/bash
# krab_oauth_refresh.sh — фоновое обновление OAuth токенов для Krab провайдеров.
# Запускается через LaunchAgent каждые 25 минут.
# Только headless провайдеры: те, у которых есть refresh_token без браузера.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
KRAB_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_BIN="$KRAB_DIR/venv/bin/python3"
LOG_FILE="/tmp/krab_oauth_refresh.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Ротация лога (оставляем последние 500 строк)
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 500 ]; then
    tail -n 500 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

log "=== OAuth refresh start ==="

# --- Gemini CLI OAuth (headless через refresh_token) ---
GEMINI_CREDS="$HOME/.gemini/oauth_creds.json"
SYNC_SCRIPT="$SCRIPT_DIR/sync_gemini_cli_oauth.py"

if [ -f "$GEMINI_CREDS" ] && [ -f "$SYNC_SCRIPT" ] && [ -x "$PYTHON_BIN" ]; then
    # Проверяем, скоро ли истечёт токен (обновляем если < 30 мин)
    EXPIRY=$(python3 -c "
import json, time, sys
try:
    with open('$GEMINI_CREDS') as f:
        d = json.load(f)
    exp = d.get('expiry_date', 0)
    # expiry_date — миллисекунды
    remaining_min = (exp / 1000 - time.time()) / 60
    print(f'{remaining_min:.0f}')
except Exception as e:
    print('-1')
" 2>/dev/null || echo "-1")

    log "Gemini CLI OAuth remaining: ${EXPIRY} min"

    if [ "$EXPIRY" -lt 30 ] 2>/dev/null; then
        log "Refreshing Gemini CLI OAuth (expires in ${EXPIRY} min)..."
        if "$PYTHON_BIN" "$SYNC_SCRIPT" > /tmp/gemini_oauth_refresh_result.json 2>&1; then
            log "Gemini CLI OAuth refreshed OK"
        else
            log "Gemini CLI OAuth refresh FAILED (manual re-login via panel :8080 needed)"
        fi
    else
        log "Gemini CLI OAuth OK (${EXPIRY} min remaining, skip refresh)"
    fi
else
    log "Gemini CLI OAuth: prerequisites missing (no creds/script/python), skipping"
fi

log "=== OAuth refresh done ==="
