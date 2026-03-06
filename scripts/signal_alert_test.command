#!/bin/bash
# =============================================================================
# Тест отправки автоалерта по текущему маршруту OPENCLAW_ALERT_*.
# Зачем: быстро проверить доставку алертов без ожидания реального инцидента.
# Связь: опциональный шаг в check_signal_alert_route.command.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

CHANNEL="${OPENCLAW_ALERT_CHANNEL:-}"
TARGET="${OPENCLAW_ALERT_TARGET:-}"
FALLBACK_CHAT_ID="${OPENCLAW_TELEGRAM_CHAT_ID:-${OWNER_TELEGRAM_ID:-}}"

if [[ -z "$CHANNEL" || -z "$TARGET" ]]; then
  echo "❌ OPENCLAW_ALERT_CHANNEL/OPENCLAW_ALERT_TARGET не заданы"
  echo "   Сначала: ./scripts/configure_alert_route.command"
  exit 2
fi

MSG="🛰️ [Krab Alert Route Test] Маршрут автоалертов активен. Время: $(date '+%Y-%m-%d %H:%M:%S')"

send_alert() {
  local target="$1"
  openclaw message send --channel "$CHANNEL" --target "$target" --message "$MSG"
}

if send_alert "$TARGET"; then
  echo "✅ Тест алерта отправлен: ${CHANNEL} -> ${TARGET}"
  exit 0
fi

if [[ "$CHANNEL" == "telegram" && "$TARGET" == @* && -n "$FALLBACK_CHAT_ID" ]]; then
  echo "⚠️ Username route не прошёл, пробую fallback chat_id..."
  if send_alert "$FALLBACK_CHAT_ID"; then
    echo "✅ Тест алерта отправлен по fallback: ${CHANNEL} -> ${FALLBACK_CHAT_ID}"
    exit 0
  fi
fi

echo "❌ Тест алерта не отправлен. Проверь route и chat_id."
echo "   Подсказка: напиши /start боту и выполни ./scripts/resolve_telegram_alert_target.command"
exit 1
