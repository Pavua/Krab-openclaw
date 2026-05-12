#!/bin/zsh
#
# Демо-запуск risk-guard модуля Краба.
# Файл нужен для macOS-сценария: двойной клик запускает безопасную проверку без
# биржевых ключей и без реальных ордеров.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x "venv/bin/python" ]]; then
  "venv/bin/python" -m src.trading.risk_guard --demo
else
  python3 -m src.trading.risk_guard --demo
fi
