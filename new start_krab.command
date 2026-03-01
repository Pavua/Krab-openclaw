#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🦀 Launching Krab Userbot..."
echo "📂 Directory: $DIR"

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

# === 0. Сброс флага остановки и зачистка конкурентов ===
rm -f .stop_krab

echo "🧹 Performing pre-flight checks..."
# Выключаем Docker-контейнер, если он работает в фоне (он мешает портам и ломает сессию)
if command -v docker &> /dev/null; then
    docker stop krab-ai-bot >/dev/null 2>&1 || true
fi

# Убиваем старые процессы бота, если они зависли
pkill -f "python.*src.main" >/dev/null 2>&1 || true

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

# === Gemini auth mode hardening ===
# Принудительно используем AI Studio API-key режим, а не Vertex/OAuth.
export GOOGLE_GENAI_USE_VERTEXAI="false"
unset GOOGLE_APPLICATION_CREDENTIALS
unset GOOGLE_CLOUD_PROJECT
unset GOOGLE_CLOUD_LOCATION
unset VERTEXAI
unset VERTEX_AI

# === OpenClaw Gateway ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
fi

if [ -n "$OPENCLAW_BIN" ]; then
    # Всегда перезапускаем gateway, чтобы применить актуальное окружение (.env).
    "$OPENCLAW_BIN" gateway stop >/dev/null 2>&1 || true
    pkill -f "openclaw-gateway" >/dev/null 2>&1 || true
    pkill -f "openclaw gateway run" >/dev/null 2>&1 || true
    pkill -f "openclaw gateway" >/dev/null 2>&1 || true
    rm -f .openclaw.pid
    sleep 1
    echo "🦞 Starting OpenClaw Gateway..."
    nohup "$OPENCLAW_BIN" gateway run > openclaw.log 2>&1 &
    echo $! > .openclaw.pid
    echo "✅ OpenClaw started (PID $!)"
    sleep 3
else
    echo "⚠️ OpenClaw binary not found. AI features may not work."
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
    clear_web_port 8080 || true

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

# === Cleanup ===
if [ -f .openclaw.pid ]; then
    PID=$(cat .openclaw.pid)
    kill "$PID" 2>/dev/null && echo "🛑 OpenClaw stopped."
    rm -f .openclaw.pid
fi

echo "🦀 Krab stopped."
read -p "Press Enter to close..."
