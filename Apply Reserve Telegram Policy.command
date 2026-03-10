#!/bin/zsh
# -*- coding: utf-8 -*-

# One-click обёртка для перевода Telegram Bot в reserve-safe режим.
# Что делает:
# - включает allowlist для DM;
# - включает allowlist для групп;
# - сохраняет replyToMode=off;
# - использует уже существующий runtime repair-скрипт без дублирования логики.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PY_BIN="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  echo "❌ Не найден виртуальный Python: $PY_BIN"
  echo "Сначала подними проектное окружение."
  read -r "?Нажми Enter для выхода..."
  exit 1
fi

echo "🦀 Применяю reserve-safe policy для Telegram Bot..."
echo "   Канал: telegram"
echo "   dmPolicy: allowlist"
echo "   groupPolicy: allowlist"
echo "   replyToMode: off"
echo

"$PY_BIN" scripts/openclaw_runtime_repair.py \
  --channels telegram \
  --dm-policy allowlist \
  --group-policy allowlist \
  --reply-to-mode off

echo
echo "✅ Reserve-safe policy применён."
echo "Если в отчёте есть 'gateway_restart_recommended: true', перезапусти runtime."
read -r "?Нажми Enter для закрытия..."
