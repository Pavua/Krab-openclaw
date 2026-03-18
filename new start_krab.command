#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)
# Назначение: детерминированный one-click запуск Krab + OpenClaw без гонок между несколькими launcher-процессами.
# Связи: используется напрямую пользователем и через Start Full Ecosystem.command.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Runtime-state переносим в per-account каталог, чтобы shared repo не держал
# lock/pid/sentinel между разными macOS-учётками.
RUNTIME_STATE_DIR="${KRAB_RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}"
LAUNCHER_LOCK_FILE="$RUNTIME_STATE_DIR/launcher.lock"
OPENCLAW_PID_FILE="$RUNTIME_STATE_DIR/openclaw.pid"
OPENCLAW_OWNER_FILE="$RUNTIME_STATE_DIR/openclaw.owner"
STOP_FLAG_FILE="$RUNTIME_STATE_DIR/stop_krab"
LEGACY_LAUNCHER_LOCK_FILE="$DIR/.krab_launcher.lock"
LEGACY_OPENCLAW_PID_FILE="$DIR/.openclaw.pid"
LEGACY_OPENCLAW_OWNER_FILE="$DIR/.openclaw.owner"
LEGACY_STOP_FLAG_FILE="$DIR/.stop_krab"
GATEWAY_OWNED_BY_THIS=0
KRAB_PROC_PATTERN="[Pp]ython.*src\\.main"
OPENCLAW_REPAIR_RESTART_RECOMMENDED=0

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
    # Останавливаем watchdog, если он был запущен этим launcher
    local wpid
    WATCHDOG_PID_FILE="${RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}/watchdog.pid"
    wpid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
    if [ -n "$wpid" ] && kill -0 "$wpid" >/dev/null 2>&1; then
        kill "$wpid" >/dev/null 2>&1 || true
    fi
}

trap cleanup_on_exit EXIT INT TERM

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

# === OpenClaw bootstrap ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
fi

if ! ensure_openclaw_account_bootstrap; then
    read -p "Press Enter to exit..."
    exit 1
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
        write_runtime_state_file "$OPENCLAW_PID_FILE" "$NEW_GATEWAY_PID" || true
        write_runtime_state_file "$OPENCLAW_OWNER_FILE" "$$" || true
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
    "$KRAB_PYTHON_BIN" -m src.main
    EXIT_CODE=$?

    # Повторная проверка после падения
    if has_stop_flag; then
        echo "🛑 Stop flag detected. Exiting..."
        clear_stop_flag
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
