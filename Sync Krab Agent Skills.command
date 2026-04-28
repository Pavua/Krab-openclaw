#!/bin/zsh
# Синхронизирует безопасный Codex dev-layer для текущей macOS-учётки.
# Не копирует auth.json, OAuth-сессии, Telegram session, browser profile и ~/.openclaw.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "== Krab Codex dev-layer sync =="
echo "user=$(whoami)"
echo "home=$HOME"
echo

if [[ "$(whoami)" == "pablito" ]]; then
  PROFILE="full"
else
  PROFILE="dev-tools"
fi

echo "profile=$PROFILE"
echo

python3 "$ROOT_DIR/scripts/sync_codex_dev_layer.py" --profile "$PROFILE"

echo
echo "Готово. Для проверки можно запустить: Check New Account Readiness.command"
if [[ -t 0 ]]; then
  read -r "?Нажми Enter для выхода..."
fi
