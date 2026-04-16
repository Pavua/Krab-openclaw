#!/bin/bash
# Login helper для Google Gemini CLI OAuth.
# Запускает gemini auth login для обновления expired OAuth token.
set -e
echo "🔑 Gemini CLI — запуск OAuth re-login..."
echo ""

if command -v gemini &>/dev/null; then
    gemini auth login
elif command -v openclaw &>/dev/null; then
    openclaw configure --section model
else
    echo "❌ Ни gemini CLI, ни openclaw не найдены в PATH."
    echo "Установите: npm i -g @anthropic-ai/gemini-cli"
    exit 1
fi

echo ""
echo "✅ OAuth token обновлён. Перезапустите gateway:"
echo "   openclaw gateway stop && openclaw gateway start"
echo ""
read -p "Нажмите Enter для выхода..."
