#!/bin/zsh
# -----------------------------------------------------------------------------
# WhatsApp Link (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Запускает процесс линковки WhatsApp через QR и сразу делает probe статуса.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "❌ openclaw CLI не найден."
  exit 1
fi

echo "📱 Запускаю WhatsApp login (появится QR в терминале/дашборде)..."
echo "1) Открой WhatsApp на телефоне"
echo "2) Linked devices -> Link a device"
echo "3) Сканируй QR"
echo

openclaw channels login --channel whatsapp

echo
echo "⏳ Проверяю статус канала..."
openclaw channels status --probe | rg -i "WhatsApp|not linked|works|error" || true

echo
echo "✅ Если видишь 'works' — канал готов."
