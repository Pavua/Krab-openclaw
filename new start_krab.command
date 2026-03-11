#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)
# Назначение: детерминированный one-click запуск Krab + OpenClaw без гонок между несколькими launcher-процессами.
# Связи: используется напрямую пользователем и через Start Full Ecosystem.command.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

LAUNCHER_LOCK_FILE="$DIR/.krab_launcher.lock"
OPENCLAW_PID_FILE="$DIR/.openclaw.pid"
OPENCLAW_OWNER_FILE="$DIR/.openclaw.owner"
GATEWAY_OWNED_BY_THIS=0
KRAB_PROC_PATTERN="[Pp]ython.*src\\.main"
OPENCLAW_REPAIR_RESTART_RECOMMENDED=0

echo "🦀 Launching Krab Userbot..."
echo "📂 Directory: $DIR"

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
    if [ -f "$LAUNCHER_LOCK_FILE" ]; then
        local prev_pid
        prev_pid="$(cat "$LAUNCHER_LOCK_FILE" 2>/dev/null || true)"
        if is_pid_alive "$prev_pid"; then
            echo "⚠️ Launcher уже запущен (PID $prev_pid). Завершаю второй экземпляр, чтобы не сломать session/runtime."
            return 1
        fi
    fi
    echo "$$" > "$LAUNCHER_LOCK_FILE"
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

cleanup_on_exit() {
    cleanup_gateway_if_owned
    release_launcher_lock
}

trap cleanup_on_exit EXIT INT TERM

# === 0. Сброс флага остановки и зачистка конкурентов ===
if ! acquire_launcher_lock; then
    read -p "Нажми Enter для закрытия окна..."
    exit 1
fi

rm -f .stop_krab

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

    echo "❌ Старый процесс Krab не завершился мягко: $pids"
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
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ Virtual environment not found (.venv or venv)!"
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    read -p "Press Enter to exit..."
    exit 1
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

# === Runtime repair OpenClaw (безопасная автопочинка перед стартом) ===
if [ -f "scripts/openclaw_runtime_repair.py" ]; then
    echo "🛠️ Repairing OpenClaw runtime config..."
    # Внешние каналы не должны держать inline reply-tag'и в пользовательском тексте.
    OPENCLAW_REPAIR_JSON="$(python3 scripts/openclaw_runtime_repair.py --dm-policy keep --reply-to-mode off 2>/dev/null || true)"
    if [ -n "${OPENCLAW_REPAIR_JSON:-}" ]; then
        export OPENCLAW_REPAIR_JSON
        OPENCLAW_REPAIR_RESTART_RECOMMENDED="$(python3 - <<'PY'
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
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
fi

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

    # Открытый порт ещё не означает живой gateway: stale-процесс может
    # держать 18789, но не отвечать на status/RPC и ломать внешние каналы.
    if is_gateway_listening && ! probe_gateway_health; then
        restart_stale_gateway "порт 18789 слушает, но health-check не проходит"
    fi

    # Если gateway уже поднят и repair не трогал state, повторно его не дёргаем.
    if is_gateway_listening && probe_gateway_health; then
        echo "✅ OpenClaw gateway уже слушает 18789, повторный старт не требуется."
        GATEWAY_OWNED_BY_THIS=0
    else
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

        echo "🦞 Starting OpenClaw Gateway..."
        # Начиная с OpenClaw 2026.3.x foreground-gateway поднимается через
        # `openclaw gateway run`. Вызов без `run` лишь печатает help и сразу
        # завершает процесс, из-за чего launcher видел "старт", но порт 18789
        # так и не начинал слушать.
        nohup "$OPENCLAW_BIN" gateway run --port 18789 > openclaw.log 2>&1 &
        NEW_GATEWAY_PID=$!
        echo "$NEW_GATEWAY_PID" > "$OPENCLAW_PID_FILE"
        echo "$$" > "$OPENCLAW_OWNER_FILE"
        GATEWAY_OWNED_BY_THIS=1
        echo "✅ OpenClaw старт-команда отправлена (PID $NEW_GATEWAY_PID)"

        if wait_gateway_listening 20 && wait_gateway_healthy 60; then
            echo "✅ OpenClaw gateway слушает порт 18789 и проходит health-check."
        else
            # OpenClaw 2026.3.x иногда успевает открыть сокет раньше, чем
            # CLI `status` начинает стабильно отвечать. Не объявляем жёсткий
            # fail мгновенно, если порт уже слушает: runtime ещё раз проверит
            # здоровье gateway своим HTTP/WebSocket контуром.
            if is_gateway_listening; then
                echo "⚠️ OpenClaw gateway уже слушает 18789, но CLI health-check ещё не стабилизировался."
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

# === Запуск бота с авто-рестартом ===
while true; do
    # Проверяем, не нажал ли пользователь Стоп
    if [ -f .stop_krab ]; then
        echo "🛑 Stop flag detected. Shutting down auto-restarter..."
        rm -f .stop_krab
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
    python -m src.main
    EXIT_CODE=$?

    # Повторная проверка после падения
    if [ -f .stop_krab ]; then
        echo "🛑 Stop flag detected. Exiting..."
        rm -f .stop_krab
        break
    fi

    if [ $EXIT_CODE -eq 42 ]; then
        echo "🔄 Restart requested (Code 42)..."
        sleep 1
        continue
    elif [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Bot stopped cleanly."
        break
    else
        echo "⚠️ Bot crashed (Code $EXIT_CODE). Restarting in 5 seconds..."
        sleep 5
    fi
done

echo "🦀 Krab stopped."
read -p "Press Enter to close..."
