#!/bin/bash
# Login helper для OpenAI Codex OAuth.
# Запускает codex auth login для обновления expired OAuth token.
set -e
echo "🔑 OpenAI Codex — запуск OAuth re-login..."
echo ""

if command -v codex &>/dev/null; then
    codex auth login
elif command -v openclaw &>/dev/null; then
    openclaw configure --section model
else
    echo "❌ Ни codex, ни openclaw CLI не найдены в PATH."
    echo "Установите: npm i -g @openai/codex"
    exit 1
fi

echo ""
echo "✅ OAuth token обновлён. Перезапустите gateway:"
echo "   openclaw gateway stop && openclaw gateway start"
echo ""
read -p "Нажмите Enter для выхода..."
