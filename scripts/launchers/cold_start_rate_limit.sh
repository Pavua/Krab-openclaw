#!/bin/bash
# Cold-start rate limit guard (anti restart-loop).
#
# Назначение:
#   Защищает от launchd KeepAlive=true respawn-loop при corrupt archive.db
#   или fatal-error в bootstrap. См. Session 26: 322 fatal_error events / 24h.
#
# Источник истины: эта же функция инлайн-вставлена в launcher
#   /Users/pablito/Antigravity_AGENTS/new\ start_krab.command
# (launcher вне git-tree, поэтому копия здесь — для доки и audit'а).
#
# Поведение:
#   - ≥5 cold starts за 5 минут → 10-мин cooldown (sleep 600)
#   - ≥10 cold starts за 5 минут → ABORT (exit 1)
#   - Лог с ts: $RUNTIME_STATE_DIR/krab_cold_starts.log
#   - Rotation: при >100 lines оставляем последние 50
#
# Override: rm "$RUNTIME_STATE_DIR/krab_cold_starts.log"
#
# DB corruption guard уже добавлен в bootstrap (commit 9d44e50) и предотвращает
# дальнейшие cycles после первого detection через sys.exit(78). Этот guard —
# второй слой (belts-and-suspenders) на launcher-уровне.

cold_start_rate_limit_check() {
    local log_file="${RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}/krab_cold_starts.log"
    local now_ts threshold_pause threshold_abort window_sec
    now_ts=$(date +%s)
    threshold_pause=5
    threshold_abort=10
    window_sec=300

    mkdir -p "$(dirname "$log_file")" || return 0
    echo "$now_ts" >> "$log_file"

    if [ -f "$log_file" ] && [ "$(wc -l < "$log_file" 2>/dev/null || echo 0)" -gt 100 ]; then
        tail -n 50 "$log_file" > "${log_file}.tmp" 2>/dev/null && mv "${log_file}.tmp" "$log_file"
    fi

    local count
    count=$(awk -v cutoff="$((now_ts - window_sec))" '$1 > cutoff' "$log_file" 2>/dev/null | wc -l | tr -d ' ')

    if [ "$count" -ge "$threshold_abort" ]; then
        echo ""
        echo "🚨 ABORT: $count cold starts за ${window_sec}s (≥${threshold_abort}). Возможна restart loop."
        echo "   Krab НЕ запускается. Проверь Sentry / archive.db / bootstrap errors."
        echo "   Override: rm '$log_file'"
        echo ""
        read -t 10 -p "Нажми Enter для закрытия окна (10s timeout)..." || true
        exit 1
    fi

    if [ "$count" -ge "$threshold_pause" ]; then
        echo ""
        echo "⚠️  COOLDOWN: $count cold starts за ${window_sec}s (≥${threshold_pause})."
        echo "   Жду 600s перед запуском, чтобы разорвать потенциальный restart loop."
        echo "   Ctrl+C чтобы прервать сейчас."
        echo ""
        sleep 600
    fi
}

# Если запущен напрямую (а не source'ed) — выполнить проверку.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    cold_start_rate_limit_check
fi
