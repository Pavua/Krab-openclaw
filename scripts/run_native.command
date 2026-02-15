#!/bin/zsh

# ------------------------------------------------------------------
# Krab Native Launcher (v5.2)
# ------------------------------------------------------------------
# Скрипт для нативного запуска Krab в macOS.
# Использует виртуальное окружение .venv_krab.
# ------------------------------------------------------------------

set -euo pipefail

# Пути
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv_krab"

cd "$PROJECT_ROOT"

echo "🦀 Запуск Krab в нативном режиме..."

# Проверка venv
if [ ! -d "$VENV_PATH" ]; then
    echo "⚠️ Виртуальное окружение не найдено в $VENV_PATH"
    echo "Пытаюсь создать новое..."
    python3 -m venv "$VENV_PATH"
    source "$VENV_PATH/bin/activate"
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source "$VENV_PATH/bin/activate"
fi

# Запуск
echo "🚀 Инициализация основного цикла..."
export PYTHONPATH="$PROJECT_ROOT"
python3 src/main.py

echo "✅ Krab остановлен."
