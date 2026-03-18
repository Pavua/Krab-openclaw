#!/bin/zsh
# Запускает Krab Voice Gateway из корня Краба.
# Voice Gateway находится в смежном репо и слушает на 127.0.0.1:8090.

set -euo pipefail

# Определяем каталог Voice Gateway относительно этого скрипта,
# либо берём из переменной окружения — работает для любого macOS-аккаунта.
_LAUNCHER_DIR="$( cd "$( dirname "$0" )" && pwd )"
GW_DIR="${KRAB_VOICE_GATEWAY_DIR:-"$( dirname "$_LAUNCHER_DIR" )/Krab Voice Gateway"}"

if [ ! -d "$GW_DIR" ]; then
    echo "❌ Voice Gateway не найден: $GW_DIR"
    read -r "?Нажмите Enter для закрытия..."
    exit 1
fi

cd "$GW_DIR"

export KRAB_VOICE_API_KEY="${KRAB_VOICE_API_KEY:-dummy_voice_key}"

# Prefer venv if present, fallback to anaconda3
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    PYTHON="/opt/homebrew/anaconda3/bin/python3"
fi

echo "🎙️ Starting Krab Voice Gateway..."
echo "📂 Directory: $GW_DIR"
echo "🐍 Python: $PYTHON"

exec "$PYTHON" -m app.main
