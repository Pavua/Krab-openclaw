#!/bin/bash
# Login helper для Google Antigravity (legacy OAuth).
# Запускает openclaw extension для обновления expired OAuth token.
set -e
echo "🔑 Google Antigravity — запуск OAuth re-login..."
echo ""

if command -v openclaw &>/dev/null; then
    openclaw configure --section model
else
    echo "❌ openclaw CLI не найден в PATH."
    exit 1
fi

echo ""
echo "✅ OAuth token обновлён. Перезапустите gateway:"
echo "   openclaw gateway stop && openclaw gateway start"
echo ""
read -p "Нажмите Enter для выхода..."
