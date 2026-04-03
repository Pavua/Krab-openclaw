#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)
# Назначение: детерминированный one-click запуск Krab + OpenClaw без гонок между несколькими launcher-процессами.
# Связи: используется напрямую пользователем и через Start Full Ecosystem.command.

_LAUNCHER_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Краб project directory — launcher may live one level above the project root
DIR="${_LAUNCHER_DIR}/Краб"
[ -d "$DIR" ] || DIR="$_LAUNCHER_DIR"
cd "$DIR"

# Runtime-state переносим в per-account каталог, чтобы shared repo не держал
# lock/pid/sentinel между разными macOS-учётками.
RUNTIME_STATE_DIR="${KRAB_RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}"
LAUNCHER_LOCK_FILE="$RUNTIME_STATE_DIR/launcher.lock"
OPENCLAW_PID_FILE="$RUNTIME_STATE_DIR/openclaw.pid"
OPENCLAW_OWNER_FILE="$RUNTIME_STATE_DIR/openclaw.owner"
STOP_FLAG_FILE="$RUNTIME_STATE_DIR/stop_krab"
KRAB_MAIN_PID_FILE="$RUNTIME_STATE_DIR/krab_main.pid"
KRAB_MAIN_WRAPPER_PID_FILE="$RUNTIME_STATE_DIR/krab_main_wrapper.pid"
KRAB_MAIN_EXIT_CODE_FILE="$RUNTIME_STATE_DIR/krab_main.exit"
KRAB_MAIN_LOG_FILE="$RUNTIME_STATE_DIR/krab_main.log"
LEGACY_LAUNCHER_LOCK_FILE="$DIR/.krab_launcher.lock"
LEGACY_OPENCLAW_PID_FILE="$DIR/.openclaw.pid"
LEGACY_OPENCLAW_OWNER_FILE="$DIR/.openclaw.owner"
LEGACY_STOP_FLAG_FILE="$DIR/.stop_krab"
GATEWAY_OWNED_BY_THIS=0
GATEWAY_JUST_RESTARTED=0
GATEWAY_WATCHDOG_PID_FILE="$RUNTIME_STATE_DIR/openclaw_gateway_watchdog.pid"
KRAB_PROC_PATTERN="[Pp]ython.*src\\.main"
OPENCLAW_REPAIR_RESTART_RECOMMENDED=0
OPENCLAW_GOD_MODE_CHANGED=0
LAUNCHER_SIGNAL_REASON=""
LAUNCHER_INTENTIONAL_STOP=0

EAR_DIR="${KRAB_EAR_DIR:-${_LAUNCHER_DIR}/Krab Ear}"
EAR_START="${EAR_DIR}/scripts/start_agent.command"
EAR_RUNTIME="${EAR_DIR}/native/runtime/KrabEarAgent"
EAR_WATCHDOG="$DIR/scripts/krab_ear_watchdog.py"
EAR_STATE_DIR="${RUNTIME_STATE_DIR}/krab_ear"
EAR_WATCHDOG_LOG="${EAR_STATE_DIR}/krab_ear_watchdog.log"
EAR_WATCHDOG_PID="${EAR_STATE_DIR}/krab_ear_watchdog.pid"
EAR_LOG="${EAR_STATE_DIR}/krab_ear_start.log"

probe_krab_ear_ready() {
    # Истину о готовности Ear берём из IPC probe, а не из мгновенного pgrep.
    if [ -f "$EAR_WATCHDOG" ] && [ -n "${KRAB_PYTHON_BIN:-}" ] && [ -x "${KRAB_PYTHON_BIN:-}" ]; then
        local probe_json=""
        probe_json="$("$KRAB_PYTHON_BIN" "$EAR_WATCHDOG" --probe --ear-dir "$EAR_DIR" 2>/dev/null || true)"
        if printf '%s' "$probe_json" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
            return 0
        fi
    fi

    if [ -x "$EAR_RUNTIME" ] && pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

wait_krab_ear_ready() {
    local timeout_sec="${1:-12}"
    local started_at
    local now

    started_at="$(date +%s)"
    while true; do
        if probe_krab_ear_ready; then
            return 0
        fi

        now="$(date +%s)"
        if [ $((now - started_at)) -ge "$timeout_sec" ]; then
            return 1
        fi

        sleep 1
    done
}

ensure_krab_ear_started() {
    mkdir -p "$EAR_STATE_DIR"

    if [ ! -x "$EAR_START" ]; then
        echo "⚠️ Не найден start скрипт Krab Ear: $EAR_START"
        return 0
    fi

    if probe_krab_ear_ready; then
        echo "🦻 Krab Ear уже запущен."
    else
        echo "🦻 Запускаю Krab Ear Agent..."
        nohup "$EAR_START" --launched-by-launchd > "$EAR_LOG" 2>&1 &
        if wait_krab_ear_ready 12; then
            echo "✅ Krab Ear Agent запущен."
        else
            echo "⚠️ Krab Ear не подтвердил IPC readiness за 12 сек. Лог: $EAR_LOG"
        fi
    fi

    if [ -f "$EAR_WATCHDOG_PID" ]; then
        WD_PID="$(cat "$EAR_WATCHDOG_PID" 2>/dev/null || true)"
        if [ -n "${WD_PID:-}" ] && kill -0 "$WD_PID" >/dev/null 2>&1; then
            echo "🛡️ Krab Ear Watchdog уже запущен (PID $WD_PID)."
            return 0
        fi
        rm -f "$EAR_WATCHDOG_PID"
    fi

    if [ -f "$EAR_WATCHDOG" ] && [ -n "${KRAB_PYTHON_BIN:-}" ] && [ -x "${KRAB_PYTHON_BIN:-}" ]; then
        echo "🛡️ Запускаю Krab Ear Watchdog..."
        nohup "$KRAB_PYTHON_BIN" "$EAR_WATCHDOG" \
          --ear-dir "$EAR_DIR" \
          --start-script "$EAR_START" \
          --runtime-bin "$EAR_RUNTIME" \
          >> "$EAR_WATCHDOG_LOG" 2>&1 &
        echo $! > "$EAR_WATCHDOG_PID"
        echo "✅ Krab Ear Watchdog запущен (PID $(cat "$EAR_WATCHDOG_PID"))."
    else
        echo "⚠️ Не удалось запустить Krab Ear Watchdog (нет python или watchdog-скрипта)."
    fi
    return 0
}

resolve_voice_gateway_dir() {
    # На `pablito` можно предпочитать локальную рабочую копию, а на USER2/USER3
    # безопаснее сначала смотреть в shared Voice Gateway, чтобы не зависеть от
    # symlink в чужой home-директории.
    local current_user
    current_user="$(id -un)"
    local candidates=()

    if [ -n "${KRAB_VOICE_GATEWAY_DIR:-}" ]; then
        candidates+=("$KRAB_VOICE_GATEWAY_DIR")
    fi

    if [ "$current_user" = "pablito" ]; then
        candidates+=(
            "${_LAUNCHER_DIR}/Krab Voice Gateway"
            "/Users/Shared/Antigravity_AGENTS/Krab Voice Gateway"
        )
    else
        candidates+=(
            "/Users/Shared/Antigravity_AGENTS/Krab Voice Gateway"
            "${_LAUNCHER_DIR}/Krab Voice Gateway"
            "/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway"
        )
    fi

    local candidate
    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        if [ -d "$candidate" ] && [ -f "$candidate/requirements.txt" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    printf '%s\n' "${KRAB_VOICE_GATEWAY_DIR:-${_LAUNCHER_DIR}/Krab Voice Gateway}"
}

VOICE_GATEWAY_DIR="$(resolve_voice_gateway_dir)"
VOICE_GATEWAY_START_SCRIPT="${VOICE_GATEWAY_DIR}/scripts/start_gateway.command"
VOICE_GATEWAY_STOP_SCRIPT="${VOICE_GATEWAY_DIR}/scripts/stop_gateway.command"

is_voice_gateway_listening() {
    lsof -t -i "tcp:8090" -sTCP:LISTEN >/dev/null 2>&1
}

probe_voice_gateway_health() {
    python3 - <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = "http://127.0.0.1:8090/health"
try:
    with urllib.request.urlopen(url, timeout=2.5) as response:
        raw = response.read().decode("utf-8", "replace")
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)

try:
    payload = json.loads(raw)
except Exception:
    payload = {}

ok = bool(payload.get("ok")) or str(payload.get("status", "")).strip().lower() in {"ok", "healthy", "up"}
raise SystemExit(0 if ok else 1)
PY
}

wait_voice_gateway_healthy() {
    local timeout_sec="${1:-18}"
    local step=0
    local max_steps=$((timeout_sec * 2))
    while [ "$step" -lt "$max_steps" ]; do
        if probe_voice_gateway_health; then
            return 0
        fi
        sleep 0.5
        step=$((step + 1))
    done
    return 1
}

ensure_voice_gateway_started() {
    if probe_voice_gateway_health; then
        echo "✅ Krab Voice Gateway уже отвечает на :8090."
        return 0
    fi
    if [ ! -x "$VOICE_GATEWAY_START_SCRIPT" ]; then
        echo "⚠️ Voice Gateway launcher не найден: $VOICE_GATEWAY_START_SCRIPT"
        return 0
    fi

    echo "🎙️ Starting Krab Voice Gateway..."
    if ! "$VOICE_GATEWAY_START_SCRIPT" >/dev/null 2>&1; then
        echo "⚠️ Не удалось запустить Krab Voice Gateway launcher."
        return 0
    fi

    if wait_voice_gateway_healthy 18; then
        echo "✅ Krab Voice Gateway слушает порт 8090 и проходит health-check."
    else
        echo "⚠️ Krab Voice Gateway не успел стать healthy на :8090. Продолжаю старт Краба без hard-fail."
    fi
    return 0
}

echo "🦀 Launching Krab Userbot..."
echo "📂 Directory: $DIR"

ensure_runtime_state_dir() {
    mkdir -p "$RUNTIME_STATE_DIR"
}

write_runtime_state_file() {
    local path="$1"
    local value="${2:-}"
    ensure_runtime_state_dir || return 1
    rm -f "$path" >/dev/null 2>&1 || true
    printf '%s' "$value" > "$path"
}

clear_stop_flag() {
    rm -f "$STOP_FLAG_FILE" "$LEGACY_STOP_FLAG_FILE" >/dev/null 2>&1 || true
}

has_stop_flag() {
    [ -f "$STOP_FLAG_FILE" ] || [ -f "$LEGACY_STOP_FLAG_FILE" ]
}

clear_krab_main_state() {
    rm -f \
        "$KRAB_MAIN_PID_FILE" \
        "$KRAB_MAIN_WRAPPER_PID_FILE" \
        "$KRAB_MAIN_EXIT_CODE_FILE" >/dev/null 2>&1 || true
}

remove_legacy_runtime_state() {
    # После миграции state-файлы не должны больше жить в общем корне repo,
    # иначе учётки снова будут мешать друг другу stale-lock'ами.
    rm -f \
        "$LEGACY_LAUNCHER_LOCK_FILE" \
        "$LEGACY_OPENCLAW_PID_FILE" \
        "$LEGACY_OPENCLAW_OWNER_FILE" \
        "$LEGACY_STOP_FLAG_FILE" >/dev/null 2>&1 || true
}

ensure_openclaw_account_bootstrap() {
    if [ -z "${OPENCLAW_BIN:-}" ]; then
        echo "⚠️ OpenClaw binary не найден; bootstrap runtime-конфига пропускается."
        return 0
    fi

    # Helper сделан idempotent: его безопасно вызывать на каждом старте.
    # Это защищает от сценария, когда `openclaw.json` уже существует, но остался
    # частично невалидным после старого onboarding/миграции между учётками.
    echo "🧱 Проверяю OpenClaw runtime для текущей macOS-учётки..."
    "$KRAB_PYTHON_BIN" scripts/openclaw_account_bootstrap.py --openclaw-bin "$OPENCLAW_BIN" || {
        echo "❌ Не удалось инициализировать ~/.openclaw для текущей учётки."
        echo "Проверь вывод scripts/openclaw_account_bootstrap.py и повтори запуск."
        return 1
    }
    return 0
}

is_pid_alive() {
    local pid="$1"
    [ -n "${pid:-}" ] && kill -0 "$pid" >/dev/null 2>&1
}

is_openclaw_gateway_pid() {
    local pid="$1"
    [ -n "${pid:-}" ] || return 1
    local cmd
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    echo "$cmd" | grep -E "openclaw( |$).*gateway( |$)|openclaw-gateway" >/dev/null 2>&1
}

acquire_launcher_lock() {
    ensure_runtime_state_dir || {
        echo "❌ Не удалось создать runtime state dir: $RUNTIME_STATE_DIR"
        return 1
    }
    if [ -f "$LAUNCHER_LOCK_FILE" ]; then
        local prev_pid
        prev_pid="$(cat "$LAUNCHER_LOCK_FILE" 2>/dev/null || true)"
        if is_pid_alive "$prev_pid"; then
            echo "⚠️ Launcher уже запущен (PID $prev_pid). Завершаю второй экземпляр, чтобы не сломать session/runtime."
            return 1
        fi
    fi
    write_runtime_state_file "$LAUNCHER_LOCK_FILE" "$$" || return 1
    return 0
}

release_launcher_lock() {
    local lock_pid
    lock_pid="$(cat "$LAUNCHER_LOCK_FILE" 2>/dev/null || true)"
    if [ "$lock_pid" = "$$" ]; then
        rm -f "$LAUNCHER_LOCK_FILE"
    fi
}

wait_gateway_listening() {
    local timeout_sec="${1:-20}"
    local step=0
    local max_steps=$((timeout_sec * 2))
    while [ "$step" -lt "$max_steps" ]; do
        if is_gateway_listening; then
            return 0
        fi
        sleep 0.5
        step=$((step + 1))
    done
    return 1
}

probe_gateway_health() {
    # Проверяем локальный health endpoint напрямую, а не парсим `openclaw status`.
    # Почему так:
    # - формат `openclaw status` менялся между релизами 2026.x;
    # - текстовый парсинг уже давал ложные "gateway unhealthy", хотя `/health`
    #   отвечал `{"ok":true,"status":"live"}` и порт реально работал.
    python3 - <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = "http://127.0.0.1:18789/health"
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        raw = response.read().decode("utf-8", "replace")
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)

try:
    payload = json.loads(raw)
except Exception:
    payload = {}

ok = bool(payload.get("ok")) or str(payload.get("status", "")).strip().lower() == "live"
raise SystemExit(0 if ok else 1)
PY
}

wait_gateway_healthy() {
    local timeout_sec="${1:-60}"
    local step=0
    local max_steps=$((timeout_sec * 2))
    while [ "$step" -lt "$max_steps" ]; do
        if probe_gateway_health; then
            return 0
        fi
        sleep 0.5
        step=$((step + 1))
    done
    return 1
}

safe_openclaw_control() {
    local timeout_sec="${1:-8}"
    shift
    local openclaw_bin="${OPENCLAW_BIN:-}"
    [ -n "$openclaw_bin" ] || return 127

    # CLI `openclaw gateway stop` в некоторых состояниях может подвисать даже
    # когда порт уже пустой. Тогда launcher зависает до бесконечности ещё до
    # старта gateway. Оборачиваем управляющие команды в жёсткий timeout.
    python3 - "$openclaw_bin" "$timeout_sec" "$@" <<'PY'
import subprocess
import sys

bin_path = sys.argv[1]
timeout_sec = float(sys.argv[2])
args = [bin_path, *sys.argv[3:]]

try:
    completed = subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_sec,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except FileNotFoundError:
    raise SystemExit(127)

raise SystemExit(int(completed.returncode))
PY
}

restart_stale_gateway() {
    local reason="${1:-stale}"
    echo "🔄 OpenClaw gateway будет перезапущен: $reason"
    "$OPENCLAW_BIN" gateway stop >/dev/null 2>&1 || true
    pkill -f "openclaw( |$).*gateway( |$)|openclaw-gateway" >/dev/null 2>&1 || true
    sleep 1
    # После kill LaunchAgent запускает новый процесс за ~1с, но инициализация
    # занимает 30–90с. Флаг предотвращает fast path "уже слушает" и заставляет
    # скрипт пройти через wait_gateway_healthy с полным ожиданием готовности.
    GATEWAY_JUST_RESTARTED=1
}

disable_legacy_launchd_core() {
    # В проекте может быть активен launchd-сервис ai.krab.core (KeepAlive=true),
    # который перезапускает src.main и ломает детерминированный one-click lifecycle.
    launchctl bootout gui/$(id -u)/ai.krab.core >/dev/null 2>&1 || true
    launchctl bootout user/$(id -u)/ai.krab.core >/dev/null 2>&1 || true
    launchctl remove ai.krab.core >/dev/null 2>&1 || true
}

# Надежная очистка порта web-панели с ожиданием освобождения.
clear_web_port() {
    local port="${1:-8080}"
    local pids
    pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
    if [ -z "$pids" ]; then
        return 0
    fi

    echo "🧹 Clearing port ${port} from old listeners: $pids"
    echo "$pids" | xargs kill -TERM 2>/dev/null || true

    # Даем процессам шанс завершиться мягко.
    local i
    for i in 1 2 3 4 5; do
        sleep 0.4
        pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
        [ -z "$pids" ] && return 0
    done

    echo "⚠️ Port ${port} still busy, forcing kill..."
    echo "$pids" | xargs kill -KILL 2>/dev/null || true
    sleep 0.6
    pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "❌ Port ${port} is still occupied after cleanup: $pids"
        return 1
    fi
    return 0
}

# Проверка, что OpenClaw gateway реально слушает нужный порт.
is_gateway_listening() {
    lsof -t -i "tcp:18789" -sTCP:LISTEN >/dev/null 2>&1
}

cleanup_gateway_if_owned() {
    # Завершаем gateway только если этот launcher реально владеет процессом.
    [ "$GATEWAY_OWNED_BY_THIS" -eq 1 ] || return 0
    local owner_pid
    owner_pid="$(cat "$OPENCLAW_OWNER_FILE" 2>/dev/null || true)"
    if [ "$owner_pid" != "$$" ]; then
        return 0
    fi
    local gw_pid
    gw_pid="$(cat "$OPENCLAW_PID_FILE" 2>/dev/null || true)"
    if is_openclaw_gateway_pid "$gw_pid"; then
        kill "$gw_pid" >/dev/null 2>&1 || true
        echo "🛑 OpenClaw остановлен владельцем launcher (PID $gw_pid)."
    fi
    rm -f "$OPENCLAW_PID_FILE" "$OPENCLAW_OWNER_FILE"
}

stop_gateway_watchdog() {
    if [ -f "$GATEWAY_WATCHDOG_PID_FILE" ]; then
        local wpid
        wpid="$(cat "$GATEWAY_WATCHDOG_PID_FILE" 2>/dev/null || true)"
        if is_pid_alive "$wpid"; then
            kill "$wpid" >/dev/null 2>&1 || true
        fi
        rm -f "$GATEWAY_WATCHDOG_PID_FILE"
    fi
}

start_manual_gateway_process() {
    [ -n "${OPENCLAW_BIN:-}" ] || return 1
    echo "🦞 Starting OpenClaw Gateway..."
    nohup "$OPENCLAW_BIN" gateway run --port 18789 > openclaw.log 2>&1 &
    NEW_GATEWAY_PID=$!
    write_runtime_state_file "$OPENCLAW_PID_FILE" "$NEW_GATEWAY_PID" || true
    write_runtime_state_file "$OPENCLAW_OWNER_FILE" "$$" || true
    GATEWAY_OWNED_BY_THIS=1
    echo "✅ OpenClaw старт-команда отправлена (PID $NEW_GATEWAY_PID)"
    return 0
}

start_gateway_watchdog() {
    [ -n "${OPENCLAW_BIN:-}" ] || return 0

    stop_gateway_watchdog

    # В ручном foreground fallback режиме у gateway нет KeepAlive.
    # Если он получает SIGTERM из-за reload/переконфига, watchdog поднимет его снова.
    (
        while true; do
            sleep 5

            if has_stop_flag; then
                exit 0
            fi

            if ! is_pid_alive "$PPID"; then
                exit 0
            fi

            if probe_gateway_health; then
                continue
            fi

            if is_gateway_listening; then
                continue
            fi

            echo "♻️ Gateway watchdog: gateway недоступен, выполняю восстановление..."
            pkill -f "openclaw( |$).*gateway( |$)|openclaw-gateway" >/dev/null 2>&1 || true
            sleep 1
            start_manual_gateway_process >/dev/null 2>&1 || true
            wait_gateway_listening 20 >/dev/null 2>&1 || true
            wait_gateway_healthy 120 >/dev/null 2>&1 || true
        done
    ) >/tmp/krab_openclaw_gateway_watchdog.log 2>&1 &

    write_runtime_state_file "$GATEWAY_WATCHDOG_PID_FILE" "$!" || true
    echo "🛡️ OpenClaw Gateway Watchdog PID: $(cat "$GATEWAY_WATCHDOG_PID_FILE" 2>/dev/null || true) (log: /tmp/krab_openclaw_gateway_watchdog.log)"
}

cleanup_on_exit() {
    if [ "$LAUNCHER_INTENTIONAL_STOP" -eq 1 ] || has_stop_flag; then
        stop_gateway_watchdog
        cleanup_gateway_if_owned
    else
        if [ -n "$LAUNCHER_SIGNAL_REASON" ]; then
            echo "ℹ️ Launcher получил $LAUNCHER_SIGNAL_REASON и завершает только управляющую оболочку."
            echo "ℹ️ Detached Krab runtime и watchdog не трогаю: это защита от ложного 'падения' из-за Terminal/session."
        fi
    fi
    release_launcher_lock
    if [ "$LAUNCHER_INTENTIONAL_STOP" -eq 1 ] || has_stop_flag; then
        # Останавливаем watchdog только при осознанном stop/restart сценарии.
        local wpid
        WATCHDOG_PID_FILE="${RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}/watchdog.pid"
        wpid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
        if [ -n "$wpid" ] && kill -0 "$wpid" >/dev/null 2>&1; then
            kill "$wpid" >/dev/null 2>&1 || true
        fi
    fi
}

handle_launcher_signal() {
    local reason="$1"
    LAUNCHER_SIGNAL_REASON="$reason"
    echo "⚠️ Launcher получил сигнал $reason."
}

trap cleanup_on_exit EXIT
trap 'handle_launcher_signal INT; exit 130' INT
trap 'handle_launcher_signal TERM; exit 143' TERM

# === 0. Сброс флага остановки и зачистка конкурентов ===
if ! acquire_launcher_lock; then
    read -p "Нажми Enter для закрытия окна..."
    exit 1
fi

clear_stop_flag
remove_legacy_runtime_state

echo "🧹 Performing pre-flight checks..."
disable_legacy_launchd_core
# Выключаем Docker-контейнер, если он работает в фоне (он мешает портам и ломает сессию)
if command -v docker &> /dev/null; then
    docker stop krab-ai-bot >/dev/null 2>&1 || true
fi

# Аккуратно завершаем старые процессы бота, чтобы не повредить session-файл.
stop_old_krab_processes() {
    local pids
    pids=$(pgrep -f "$KRAB_PROC_PATTERN" || true)
    if [ -z "$pids" ]; then
        return 0
    fi

    echo "🧹 Found old Krab processes: $pids"
    echo "$pids" | xargs kill -TERM >/dev/null 2>&1 || true
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        sleep 0.4
        pids=$(pgrep -f "$KRAB_PROC_PATTERN" || true)
        [ -z "$pids" ] && return 0
    done

    echo "⚠️ Старый процесс Krab не завершился мягко: $pids"
    echo "🪓 Применяю принудительную остановку, чтобы one-click старт не зависал."
    echo "$pids" | xargs kill -KILL >/dev/null 2>&1 || true

    for i in 1 2 3 4 5; do
        sleep 0.4
        pids=$(pgrep -f "$KRAB_PROC_PATTERN" || true)
        [ -z "$pids" ] && return 0
    done

    echo "❌ Даже после SIGKILL старый процесс Krab остался жив: $pids"
    echo "Запусти 'new Stop Krab.command' и повтори запуск."
    return 1
}

if ! stop_old_krab_processes; then
    read -p "Нажми Enter для закрытия окна..."
    exit 1
fi

# Чистим порт web-панели до первого запуска.
clear_web_port 8080 || true

# === Виртуальное окружение ===
python_supports_module() {
    local python_bin="$1"
    local module_name="$2"
    [ -x "$python_bin" ] || return 1
    "$python_bin" - "$module_name" <<'PY' >/dev/null 2>&1
import importlib.util
import sys

module_name = str(sys.argv[1] or "").strip()
raise SystemExit(0 if module_name and importlib.util.find_spec(module_name) else 1)
PY
}

select_krab_python_env() {
    # Выбираем не "первое попавшееся" окружение, а то, где есть runtime-модули
    # и, по возможности, `mlx_whisper` для voice/STT-контура.
    eval "$(
        python3 - "$DIR" <<'PY'
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
candidates = [
    root / "venv" / "bin" / "python",
    root / ".venv" / "bin" / "python",
]
runtime_modules = ("pyrogram", "google.genai", "PIL")
stt_modules = ("mlx_whisper",)


def supports(python_bin: Path, module_name: str) -> bool:
    if not python_bin.exists():
        return False
    try:
        completed = subprocess.run(
            [
                str(python_bin),
                "-c",
                (
                    "import importlib.util, sys; "
                    "raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
                ),
                module_name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0


def score_runtime(python_bin: Path) -> tuple[int, int, int]:
    runtime_score = sum(1 for module in runtime_modules if supports(python_bin, module))
    stt_score = sum(1 for module in stt_modules if supports(python_bin, module))
    exists_score = 1 if python_bin.exists() else 0
    return runtime_score, stt_score, exists_score


best_runtime = None
best_runtime_score = (-1, -1, -1)
best_stt = None
best_stt_score = (-1, -1, -1)

for candidate in candidates:
    runtime_score = score_runtime(candidate)
    stt_score = (runtime_score[1], runtime_score[0], runtime_score[2])
    if runtime_score > best_runtime_score:
        best_runtime = candidate
        best_runtime_score = runtime_score
    if stt_score > best_stt_score:
        best_stt = candidate
        best_stt_score = stt_score

if best_runtime is None or best_runtime_score[2] <= 0:
    raise SystemExit(1)

if best_stt is None or best_stt_score[2] <= 0:
    best_stt = best_runtime

print(f'KRAB_PYTHON_BIN="{best_runtime}"')
print(f'KRAB_VENV_DIR="{best_runtime.parent.parent}"')
print(f'KRAB_STT_PYTHON_BIN="{best_stt}"')
PY
    )"
}

if ! select_krab_python_env; then
    echo "❌ Virtual environment not found (ожидался venv или .venv)!"
    echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    read -p "Press Enter to exit..."
    exit 1
fi

if [ -f "$KRAB_VENV_DIR/bin/activate" ]; then
    # Активируем выбранное окружение, чтобы subprocess-слой и `python` совпадали
    # с тем Python, который мы только что детерминированно выбрали.
    source "$KRAB_VENV_DIR/bin/activate"
fi

export KRAB_PYTHON_BIN
export KRAB_STT_PYTHON_BIN

echo "🐍 Runtime Python: $KRAB_PYTHON_BIN"
if [ "${KRAB_STT_PYTHON_BIN:-}" != "${KRAB_PYTHON_BIN:-}" ]; then
    echo "🎙️ STT Python: $KRAB_STT_PYTHON_BIN"
fi

# === Загрузка .env ===
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "⚠️ .env file not found!"
fi

# Если флаг scheduler явно не задан, включаем runtime-reminders по умолчанию.
# Это сохраняет стабильное поведение после миграций/чисток .env.
if [ -z "${SCHEDULER_ENABLED:-}" ]; then
    export SCHEDULER_ENABLED=1
fi

# === Gemini auth mode hardening ===
# Принудительно используем AI Studio API-key режим, а не Vertex/OAuth.
export GOOGLE_GENAI_USE_VERTEXAI="false"
unset GOOGLE_APPLICATION_CREDENTIALS
unset GOOGLE_CLOUD_PROJECT
unset GOOGLE_CLOUD_LOCATION
unset VERTEXAI
unset VERTEX_AI

# Если есть платный Gemini ключ — пробрасываем его в GOOGLE_API_KEY,
# потому что OpenClaw provider 'google' всегда предпочитает GOOGLE_API_KEY над GEMINI_API_KEY.
# Бесплатный ключ (GOOGLE_API_KEY в .env) перезаписывается здесь намеренно.
if [ -n "${GEMINI_API_KEY_PAID:-}" ]; then
    export GOOGLE_API_KEY="$GEMINI_API_KEY_PAID"
fi

ensure_voice_gateway_started

ensure_krab_ear_started

# === OpenClaw bootstrap ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
fi

if ! ensure_openclaw_account_bootstrap; then
    read -p "Press Enter to exit..."
    exit 1
fi

# === OpenClaw config doctor (авто-починка валидации конфига) ===
# Исправляет устаревшие поля и stale-плагины, которые ломают валидацию
# после обновлений OpenClaw. Запускаем до repair и старта gateway,
# чтобы стартовый цикл не застревал из-за плохого конфига.
if [ -n "${OPENCLAW_BIN:-}" ]; then
    echo "🩺 OpenClaw doctor --fix..."
    "$OPENCLAW_BIN" doctor --fix >/dev/null 2>&1 || true
fi

# === OpenClaw God Mode sync (exec policy + host approvals) ===
# После OpenClaw 2026.4.x unrestricted exec собирается из двух файлов:
# `openclaw.json` и `exec-approvals.json`. Если править только один слой,
# агент продолжает ловить `allowlist miss` даже при `tools.exec.security=full`.
if [ -f "scripts/openclaw_god_mode_sync.py" ]; then
    echo "👑 Syncing OpenClaw God Mode..."
    OPENCLAW_GOD_MODE_JSON="$("$KRAB_PYTHON_BIN" scripts/openclaw_god_mode_sync.py 2>/dev/null || true)"
    if [ -n "${OPENCLAW_GOD_MODE_JSON:-}" ]; then
        export OPENCLAW_GOD_MODE_JSON
        OPENCLAW_GOD_MODE_CHANGED="$("$KRAB_PYTHON_BIN" - <<'PY'
import json
import os

raw = str(os.environ.get("OPENCLAW_GOD_MODE_JSON", "") or "").strip()
changed = 0
if raw:
    try:
        payload = json.loads(raw)
        changed = 1 if bool(payload.get("changed", False)) else 0
    except Exception:
        changed = 0
print(changed)
PY
)"
        unset OPENCLAW_GOD_MODE_JSON
    fi
fi

# === Runtime repair OpenClaw (безопасная автопочинка перед стартом) ===
if [ -f "scripts/openclaw_runtime_repair.py" ]; then
    echo "🛠️ Repairing OpenClaw runtime config..."
    # Внешние каналы не должны держать inline reply-tag'и в пользовательском тексте.
    OPENCLAW_REPAIR_JSON="$("$KRAB_PYTHON_BIN" scripts/openclaw_runtime_repair.py --dm-policy keep --reply-to-mode off 2>/dev/null || true)"
    if [ -n "${OPENCLAW_REPAIR_JSON:-}" ]; then
        export OPENCLAW_REPAIR_JSON
        OPENCLAW_REPAIR_RESTART_RECOMMENDED="$("$KRAB_PYTHON_BIN" - <<'PY'
import json
import os

raw = str(os.environ.get("OPENCLAW_REPAIR_JSON", "") or "").strip()
recommended = 0
if raw:
    try:
        payload = json.loads(raw)
        recommended = 1 if bool(payload.get("gateway_restart_recommended", False)) else 0
    except Exception:
        recommended = 0
print(recommended)
PY
)"
        unset OPENCLAW_REPAIR_JSON
    fi
fi

# === OpenClaw Gateway ===
if [ -n "$OPENCLAW_BIN" ]; then
    # Отключаем lab-демон, который может автоподниматься на 18890 и ломать единый runtime.
    launchctl remove ai.openclaw.lab >/dev/null 2>&1 || true
    launchctl bootout gui/$(id -u)/ai.openclaw.lab >/dev/null 2>&1 || true
    launchctl bootout user/$(id -u)/ai.openclaw.lab >/dev/null 2>&1 || true

    # Если repair реально менял runtime-файлы, живой gateway нужно перезапустить,
    # иначе он может вернуть старые in-memory sessions и откатить fix.
    if is_gateway_listening && [ "${OPENCLAW_REPAIR_RESTART_RECOMMENDED:-0}" = "1" ]; then
        restart_stale_gateway "repair изменил runtime-состояние"
    fi

    if is_gateway_listening && [ "${OPENCLAW_GOD_MODE_CHANGED:-0}" = "1" ]; then
        restart_stale_gateway "god mode sync изменил exec policy"
    fi

    # Открытый порт ещё не означает живой gateway: stale-процесс может
    # держать 18789, но не отвечать на status/RPC и ломать внешние каналы.
    if is_gateway_listening && ! probe_gateway_health; then
        restart_stale_gateway "порт 18789 слушает, но health-check не проходит"
    fi

    # Авто-загрузка LaunchAgent если plist установлен, но не загружен в launchd.
    # Загружаем ТОЛЬКО когда gateway не запущен: если gateway уже слушает порт,
    # RunAtLoad=true попытается поднять второй экземпляр → port conflict → crash loop.
    _OC_LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
    if [ -f "$_OC_LAUNCHAGENT_PLIST" ] && ! launchctl list ai.openclaw.gateway >/dev/null 2>&1; then
        if ! is_gateway_listening; then
            echo "📋 LaunchAgent ai.openclaw.gateway установлен, но не загружен. Загружаю..."
            launchctl load "$_OC_LAUNCHAGENT_PLIST" && echo "✅ LaunchAgent загружен (gateway стартует через launchd)" || \
                echo "⚠️ Не удалось загрузить LaunchAgent, продолжаю с nohup-fallback."
            sleep 1
        fi
        # Если gateway уже слушает — не загружаем, fast path сработает ниже.
    fi

    # Определяем, управляет ли LaunchAgent жизненным циклом gateway.
    # Если да — не запускаем nohup вручную и не делаем cleanup_gateway_if_owned.
    # LaunchAgent гарантирует KeepAlive на уровне launchd; ручной nohup создал бы
    # конкурирующий процесс, не подконтрольный launchd.
    LAUNCHAGENT_MANAGES_GATEWAY=0
    if launchctl list ai.openclaw.gateway >/dev/null 2>&1; then
        LAUNCHAGENT_MANAGES_GATEWAY=1
        echo "🤖 OpenClaw gateway управляется LaunchAgent (ai.openclaw.gateway)."
    fi

    # Если gateway уже поднят и repair не трогал state, повторно его не дёргаем.
    # После принудительного перезапуска (GATEWAY_JUST_RESTARTED=1) fast path пропускается:
    # LaunchAgent поднял новый процесс за ~1с и порт слушает, но инициализация занимает
    # 30–90с. Без ожидания Краб стартует пока gateway в crash-loop и получает ConnectError.
    if [ "${GATEWAY_JUST_RESTARTED:-0}" -eq 0 ] && is_gateway_listening && probe_gateway_health; then
        echo "✅ OpenClaw gateway уже слушает 18789, повторный старт не требуется."
        GATEWAY_OWNED_BY_THIS=0
    elif [ "$LAUNCHAGENT_MANAGES_GATEWAY" -eq 1 ]; then
        # LaunchAgent управляет gateway — просим launchd поднять его, если ещё не поднят.
        # cleanup_on_exit намеренно НЕ убивает gateway (GATEWAY_OWNED_BY_THIS=0):
        # инфраструктура должна жить независимо от жизненного цикла Krab-бота.
        echo "🦞 Запрашиваю старт OpenClaw gateway через LaunchAgent..."
        launchctl kickstart "gui/$(id -u)/ai.openclaw.gateway" >/dev/null 2>&1 \
            || launchctl start "ai.openclaw.gateway" >/dev/null 2>&1 \
            || true
        GATEWAY_OWNED_BY_THIS=0

        # После restart wait увеличен до 120с: launchd может входить в exponential
        # backoff после нескольких быстрых падений (port CLOSE_WAIT, lock files).
        _gw_wait_sec=60
        if [ "${GATEWAY_JUST_RESTARTED:-0}" -eq 1 ]; then
            _gw_wait_sec=120
        fi
        if wait_gateway_listening 20 && wait_gateway_healthy "$_gw_wait_sec"; then
            echo "✅ OpenClaw gateway слушает порт 18789 и проходит health-check."
            # После health-check — ждём 5с и проверяем стабильность.
            # OpenClaw может перезапустить себя через ~2с если файловый вотчер
            # увидел изменения конфига (openclaw_runtime_repair.py). После self-SIGTERM
            # launchd входит в crash-loop ~3 мин. Ждём стабилизации перед стартом Краба.
            if [ "${GATEWAY_JUST_RESTARTED:-0}" -eq 1 ]; then
                sleep 5
                if ! probe_gateway_health; then
                    echo "⏳ Gateway перезапустился (конфиг-реакция). Ждём стабилизации (до 5 мин)..."
                    wait_gateway_listening 120 && wait_gateway_healthy 180 \
                        && echo "✅ OpenClaw gateway стабилизировался." \
                        || echo "⚠️ OpenClaw gateway не стабилизировался. Краб попытается подключиться позже."
                else
                    echo "✅ OpenClaw gateway стабилен."
                fi
            fi
        else
            if is_gateway_listening; then
                echo "⚠️ OpenClaw gateway уже слушает 18789, но health-check ещё не стабилизировался."
            else
                echo "❌ OpenClaw gateway не стартовал. Проверь ~/.openclaw/logs/gateway.log"
            fi
        fi
    else
        # LaunchAgent не установлен — поднимаем gateway вручную (fallback-режим).
        # Мягко чистим хвосты только явного stale-процесса из PID файла.
        if [ -f "$OPENCLAW_PID_FILE" ]; then
            STALE_PID="$(cat "$OPENCLAW_PID_FILE" 2>/dev/null || true)"
            if is_openclaw_gateway_pid "$STALE_PID"; then
                kill "$STALE_PID" >/dev/null 2>&1 || true
                sleep 0.5
            fi
        fi

        stop_rc=0
        safe_openclaw_control 8 gateway stop || stop_rc=$?
        if [ "$stop_rc" -eq 124 ]; then
            echo "⚠️ openclaw gateway stop завис; продолжаю через принудительную зачистку stale-процесса."
            pkill -f "openclaw( |$).*gateway( |$)|openclaw-gateway" >/dev/null 2>&1 || true
        fi

        # Начиная с OpenClaw 2026.3.x foreground-gateway поднимается через
        # `openclaw gateway run`. Вызов без `run` лишь печатает help и сразу
        # завершает процесс, из-за чего launcher видел "старт", но порт 18789
        # так и не начинал слушать.
        start_manual_gateway_process

        if wait_gateway_listening 20 && wait_gateway_healthy 120; then
            echo "✅ OpenClaw gateway слушает порт 18789 и проходит health-check."
            start_gateway_watchdog
        else
            # OpenClaw 2026.3.x иногда успевает открыть сокет раньше, чем
            # CLI `status` начинает стабильно отвечать. Не объявляем жёсткий
            # fail мгновенно, если порт уже слушает: runtime ещё раз проверит
            # здоровье gateway своим HTTP/WebSocket контуром.
            if is_gateway_listening; then
                echo "⚠️ OpenClaw gateway уже слушает 18789, но CLI health-check ещё не стабилизировался."
                start_gateway_watchdog
            else
                echo "❌ OpenClaw gateway не прошёл health-check после старта. Проверь openclaw.log."
            fi
        fi
    fi
else
    echo "⚠️ OpenClaw binary not found. AI features may not work."
fi

# === Browser Relay (OpenClaw Browser) ===
# По умолчанию не поднимаем relay-browser автоматически:
# это открывает отдельное automation Chrome окно и мешает основному рабочему профилю.
# Если нужен старый eager-start режим для acceptance/debug, включается явно через env.
OPENCLAW_BROWSER_AUTOSTART_VALUE="${OPENCLAW_BROWSER_AUTOSTART:-0}"
case "$(printf '%s' "$OPENCLAW_BROWSER_AUTOSTART_VALUE" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
        OPENCLAW_BROWSER_AUTOSTART_ENABLED=1
        ;;
    *)
        OPENCLAW_BROWSER_AUTOSTART_ENABLED=0
        ;;
esac

if [ -n "$OPENCLAW_BIN" ] && is_gateway_listening; then
    if [ "$OPENCLAW_BROWSER_AUTOSTART_ENABLED" -eq 1 ]; then
        echo "🌐 OpenClaw Browser Relay: автозапуск включён через OPENCLAW_BROWSER_AUTOSTART=1"
        "$OPENCLAW_BIN" browser start >/dev/null 2>&1 || true
    else
        echo "ℹ️ OpenClaw Browser Relay: автозапуск отключён. Для eager-start выставь OPENCLAW_BROWSER_AUTOSTART=1"
    fi
fi

# === Chrome CDP (Remote Debugging Port 9222) ===
# Запускает Chrome с отдельным debug-профилем на порту 9222 для browser-инструментов.
# Не трогает основной Chrome профиль с закладками/расширениями пользователя.
# Профиль хранится в ~/Library/Application Support/ChromeDebugProfile (постоянный).
CHROME_DEBUG_PROFILE="$HOME/Library/Application Support/ChromeDebugProfile"
CHROME_DEBUG_PORT=9222

_is_chrome_cdp_listening() {
    curl -s --max-time 2 "http://127.0.0.1:${CHROME_DEBUG_PORT}/json/version" >/dev/null 2>&1
}

if ! _is_chrome_cdp_listening; then
    if [ -d "/Applications/Google Chrome.app" ]; then
        echo "🌐 Запускаю Chrome с debug-профилем (порт ${CHROME_DEBUG_PORT})..."
        mkdir -p "$CHROME_DEBUG_PROFILE"
        open -n -a "Google Chrome" --args \
            --remote-debugging-port="${CHROME_DEBUG_PORT}" \
            --remote-allow-origins='*' \
            --user-data-dir="$CHROME_DEBUG_PROFILE" \
            --no-first-run \
            --no-default-browser-check \
            >/dev/null 2>&1 &
        # Ждём до 10 сек пока порт не откроется
        _cdp_wait=0
        while [ $_cdp_wait -lt 10 ]; do
            sleep 1
            _cdp_wait=$((_cdp_wait + 1))
            if _is_chrome_cdp_listening; then
                echo "✅ Chrome CDP готов на порту ${CHROME_DEBUG_PORT}"
                break
            fi
        done
        if ! _is_chrome_cdp_listening; then
            echo "⚠️ Chrome CDP не ответил за 10 сек. Browser-инструменты могут не работать."
        fi
    else
        echo "ℹ️ Google Chrome не найден, пропускаю CDP-запуск."
    fi
else
    echo "✅ Chrome CDP уже слушает порт ${CHROME_DEBUG_PORT}"
fi

# === Claude Proxy ===
# Запускает claude-proxy: OpenAI-compatible API сервер через claude.ai сессию.
CLAUDE_PROXY_PID_FILE="$RUNTIME_STATE_DIR/claude_proxy.pid"

start_claude_proxy() {
    local proxy_script="$DIR/scripts/claude_proxy_server.py"
    if [ ! -f "$proxy_script" ]; then
        echo "⚠️ claude_proxy_server.py не найден, пропускаю."
        return 0
    fi
    if lsof -t -i "tcp:17191" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✅ Claude Proxy уже слушает :17191."
        return 0
    fi
    echo "🤖 Запускаю Claude Proxy..."
    nohup "$KRAB_PYTHON_BIN" "$proxy_script" >> /tmp/claude_proxy.log 2>&1 &
    local pid=$!
    write_runtime_state_file "$CLAUDE_PROXY_PID_FILE" "$pid" || true
    sleep 2
    if lsof -t -i "tcp:17191" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✅ Claude Proxy слушает :17191 (PID $pid)."
    else
        echo "⚠️ Claude Proxy не поднялся на :17191. Проверь /tmp/claude_proxy.log"
    fi
}

start_claude_proxy

# === Gemini CLI OAuth Sync ===
# Обновляет access token из refresh_token без браузера (headless).
refresh_gemini_oauth() {
    local sync_script="$DIR/scripts/sync_gemini_cli_oauth.py"
    if [ ! -f "$sync_script" ]; then
        return 0
    fi
    if [ ! -f "$HOME/.gemini/oauth_creds.json" ]; then
        return 0
    fi
    echo "🔑 Обновляю Gemini CLI OAuth токен..."
    if "$KRAB_PYTHON_BIN" "$sync_script" >/dev/null 2>&1; then
        echo "✅ Gemini CLI OAuth обновлён."
    else
        # Не fail — Krab запустится и без актуального токена
        echo "ℹ️ Gemini CLI OAuth sync пропущен (перелогинься через панель :8080 если нужно)."
    fi
}

refresh_gemini_oauth

# === Telegram Session Watchdog ===
# Мониторит /api/health/lite и перезапускает userbot при деградации сессии.
WATCHDOG_PID_FILE="$RUNTIME_STATE_DIR/watchdog.pid"

stop_old_watchdog() {
    if [ -f "$WATCHDOG_PID_FILE" ]; then
        local wpid
        wpid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
        if is_pid_alive "$wpid"; then
            kill "$wpid" >/dev/null 2>&1 || true
        fi
        rm -f "$WATCHDOG_PID_FILE"
    fi
}

start_watchdog() {
    stop_old_watchdog
    nohup "$KRAB_PYTHON_BIN" "$DIR/scripts/telegram_session_watchdog.py" >> /tmp/krab_session_watchdog.log 2>&1 &
    local wpid=$!
    write_runtime_state_file "$WATCHDOG_PID_FILE" "$wpid" || true
    echo "👁️ Session Watchdog PID: $wpid (log: /tmp/krab_session_watchdog.log)"
}

start_watchdog

start_krab_main_detached() {
    ensure_runtime_state_dir || return 1
    clear_krab_main_state
    {
        echo
        echo "==== Krab detached start $(date '+%Y-%m-%d %H:%M:%S') ===="
    } >> "$KRAB_MAIN_LOG_FILE"

    "$KRAB_PYTHON_BIN" - "$KRAB_PYTHON_BIN" "$DIR" "$KRAB_MAIN_LOG_FILE" "$KRAB_MAIN_PID_FILE" "$KRAB_MAIN_EXIT_CODE_FILE" <<'PY' &
import os
import subprocess
import sys
import time

python_bin, workdir, log_path, pid_path, exit_path = sys.argv[1:6]

os.makedirs(os.path.dirname(log_path), exist_ok=True)
os.setsid()

with open(log_path, "a", buffering=1, encoding="utf-8") as log_fp:
    log_fp.write(f"[launcher] detached_wrapper_started pid={os.getpid()} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_fp.flush()
    proc = subprocess.Popen(
        [python_bin, "-m", "src.main"],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
    )
    with open(pid_path, "w", encoding="utf-8") as pid_fp:
        pid_fp.write(str(proc.pid))
    rc = proc.wait()
    with open(exit_path, "w", encoding="utf-8") as exit_fp:
        exit_fp.write(str(rc))
    log_fp.write(f"[launcher] detached_wrapper_finished pid={os.getpid()} child_pid={proc.pid} rc={rc} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_fp.flush()
PY
    local wrapper_pid=$!
    write_runtime_state_file "$KRAB_MAIN_WRAPPER_PID_FILE" "$wrapper_pid" || true
    echo "$wrapper_pid"
}

stop_krab_main_detached() {
    local wrapper_pid="$1"
    if [ -z "$wrapper_pid" ] && [ -f "$KRAB_MAIN_WRAPPER_PID_FILE" ]; then
        wrapper_pid="$(cat "$KRAB_MAIN_WRAPPER_PID_FILE" 2>/dev/null || true)"
    fi
    if [ -n "$wrapper_pid" ] && kill -0 "$wrapper_pid" >/dev/null 2>&1; then
        echo "🛑 Останавливаю detached Krab session (wrapper PID $wrapper_pid)..."
        kill -TERM -- "-$wrapper_pid" >/dev/null 2>&1 || kill -TERM "$wrapper_pid" >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            sleep 1
            if ! kill -0 "$wrapper_pid" >/dev/null 2>&1; then
                break
            fi
        done
        if kill -0 "$wrapper_pid" >/dev/null 2>&1; then
            echo "⚠️ Detached wrapper не завершился мягко, применяю SIGKILL."
            kill -KILL -- "-$wrapper_pid" >/dev/null 2>&1 || kill -KILL "$wrapper_pid" >/dev/null 2>&1 || true
        fi
    fi
}

wait_for_krab_main_detached() {
    local wrapper_pid="$1"
    while kill -0 "$wrapper_pid" >/dev/null 2>&1; do
        if has_stop_flag; then
            stop_krab_main_detached "$wrapper_pid"
            LAUNCHER_INTENTIONAL_STOP=1
            wait "$wrapper_pid" >/dev/null 2>&1 || true
            clear_krab_main_state
            return 0
        fi
        sleep 1
    done

    wait "$wrapper_pid"
    return $?
}

# === Запуск бота с авто-рестартом ===
while true; do
    # Проверяем, не нажал ли пользователь Стоп
    if has_stop_flag; then
        echo "🛑 Stop flag detected. Shutting down auto-restarter..."
        clear_stop_flag
        break
    fi

    # Превентивная зачистка зависшего порта 8080.
    # Если порт не освобождается — не пытаемся стартовать, чтобы не зациклить relogin.
    if ! clear_web_port 8080; then
        echo "⚠️ Не удалось освободить 8080. Повторная попытка через 3 секунды..."
        sleep 3
        continue
    fi

    echo "🚀 Starting Krab..."
    echo "🧾 Лог detached runtime: $KRAB_MAIN_LOG_FILE"
    WRAPPER_PID="$(start_krab_main_detached)"
    echo "🧠 Detached wrapper PID: $WRAPPER_PID"
    wait_for_krab_main_detached "$WRAPPER_PID"
    EXIT_CODE=$?
    clear_krab_main_state

    # Повторная проверка после падения
    if has_stop_flag; then
        echo "🛑 Stop flag detected. Exiting..."
        LAUNCHER_INTENTIONAL_STOP=1
        clear_stop_flag
        break
    fi

    if [ $EXIT_CODE -eq 42 ]; then
        echo "🔄 Restart requested (Code 42)..."
        sleep 1
        continue
    elif [ $EXIT_CODE -eq 0 ]; then
        LAUNCHER_INTENTIONAL_STOP=1
        echo "✅ Bot stopped cleanly."
        break
    else
        echo "⚠️ Bot crashed (Code $EXIT_CODE). Restarting in 5 seconds..."
        echo "📄 Последние строки runtime-лога:"
        tail -n 20 "$KRAB_MAIN_LOG_FILE" 2>/dev/null || true
        sleep 5
    fi
done

echo "🦀 Krab stopped."
read -p "Press Enter to close..."
