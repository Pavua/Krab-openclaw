#!/bin/bash
# -*- coding: utf-8 -*-

# Krab Voice Assistant Launcher (Phase 15.3)
# Этот скрипт запускает Krab в режиме активного голосового ассистента.

cd "$(dirname "$0")/.." || exit

echo "🎙️ Запускаю Krab Voice Assistant v2..."
echo "---"

# Проверка окружения
if [ ! -f "venv/bin/activate" ]; then
    echo "❌ Виртуальное окружение не найдено. Сначала запусти install.command"
    exit 1
fi

source venv/bin/activate

# Запуск основного процесса с флагом VOICE_MODE
export VOICE_MODE=1
export PYTHONUNBUFFERED=1

python3 src/main.py

echo "---"
echo "🏁 Сессия завершена."
pause
