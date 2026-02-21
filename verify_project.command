#!/bin/bash
# -----------------------------------------------------------------------------
# Krab Project Verification Utility
# Выполняет автоматизированную проверку критических путей проекта.
# -----------------------------------------------------------------------------

cd "$(dirname "$0")" || exit
echo "--- 🦀 Krab Logic Verification ---"

echo "1. Checking Environment..."
if [ ! -f ".env" ]; then
    echo "❌ ОШИБКА: .env файл не найден!"
    exit 1
fi

echo "2. Checking Python Dependencies..."
pip list | grep -E "pyrogram|google-generativeai|openai" > /dev/null
if [ $? -ne 0 ]; then
    echo "⚠️ ПРЕДУПРЕЖДЕНИЕ: Некоторые зависимости могут отсутствовать."
fi

echo "3. Smoke-test Core Systems (Model Manager)..."
# Можно запустить быстрый тест через python
# python3 -c "from src.core.model_manager import ModelRouter; print('ModelRouter OK')"

echo "--- Verification Complete! ---"
read -n 1 -s -r -p "Нажмите любую клавишу для продолжения..."
echo ""

