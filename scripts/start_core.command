#!/bin/bash
# -*- coding: utf-8 -*-

# Скрипт для перезапуска ядра Краба
# Используется Watchdog для самовосстановления

cd "/Users/pablito/Antigravity_AGENTS/Краб"

echo "♻️ Перезапуск Krab Core..."

# Убиваем старые процессы (на всякий случай)
pkill -f "python3 src/main.py" || true

# Запуск в новом окне терминала для наглядности (опционально)
# osascript -e 'tell app "Terminal" to do script "cd /Users/pablito/Antigravity_AGENTS/Краб && source .venv_krab/bin/activate && python3 src/main.py"'

# Или просто запуск в текущем контексте (Watchdog запускает этот скрипт через subprocess)
export PYTHONPATH=$PYTHONPATH:$(pwd)
source .venv_krab/bin/activate
nohup python3 src/main.py > logs/core_restart.log 2>&1 &

echo "✅ Krab Core запущен в фоне."
