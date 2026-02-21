#!/bin/zsh

# 🦀 Krab v5.1 Dependency Installer for macOS
# Этот скрипт устанавливает все необходимые библиотеки для работы Singularity (MCP, Swarm, Monitor).

echo "🚀 Установка зависимостей Krab v5.1 Singularity..."

# Переходим в директорию скрипта
cd "$(dirname "$0")"/..

# Проверяем наличие venv
if [ ! -d ".venv" ]; then
    echo "📦 Создание виртуального окружения..."
    python3 -m venv .venv
fi

# Активируем и обновляем pip
source .venv/bin/activate
pip install --upgrade pip

# Устанавливаем основное
echo "📚 Установка основных пакетов..."
pip install pyrogram tgcrypto structlog psutil mcp aiohttp chromadb pdfplumber python-docx openpyxl mlx-whisper apscheduler google-generativeai pydantic-settings

# Если есть requirements.txt
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

echo "✅ Все зависимости установлены. Теперь ты можешь запустить бота через run_krab.command"
read -k1 -s "?Нажми любую клавишу для выхода..."
