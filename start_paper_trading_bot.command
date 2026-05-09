#!/bin/zsh
# Запускает виртуального крипто-бота Краба одним кликом на macOS.
# Скрипт не использует реальные биржевые ключи: только публичные цены и paper trading.

set -euo pipefail

cd "$(dirname "$0")"

if [[ -d ".venv" ]]; then
  source ".venv/bin/activate"
fi

python3 scripts/run_paper_trading_bot.py

echo ""
echo "Отчёт сохранён: output/paper_trading_report.md"
echo "Состояние портфеля: data/paper_trading_state.json"
read -r "?Нажми Enter, чтобы закрыть окно..."

