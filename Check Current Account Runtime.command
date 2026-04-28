#!/bin/zsh
# Показывает, безопасно ли текущей учётке трогать live runtime Krab.
# Helper-учётки по умолчанию должны оставаться в code-only/dev-admin режиме.

set -euo pipefail

echo "== Krab runtime ownership check =="
echo "user=$(whoami)"
echo "home=$HOME"
echo

echo "-- launchctl krab --"
launchctl list 2>/dev/null | grep -i krab || true

echo
echo "-- local ports --"
lsof -nP -iTCP:8080 -sTCP:LISTEN 2>/dev/null || true
lsof -nP -iTCP:18789 -sTCP:LISTEN 2>/dev/null || true
lsof -nP -iTCP:18800 -sTCP:LISTEN 2>/dev/null || true

echo
if [[ "$(whoami)" != "pablito" ]]; then
  echo "ВНИМАНИЕ: это helper-учётка. Не запускай второй Krab runtime поверх live owner без отдельного reclaim/freeze."
else
  echo "pablito: можно выполнять runtime-admin действия, если это соответствует текущей задаче."
fi

if [[ -t 0 ]]; then
  read -r "?Нажми Enter для выхода..."
fi
