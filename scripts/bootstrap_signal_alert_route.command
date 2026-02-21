#!/bin/zsh
# Bootstrap маршрута Signal-алертов в Telegram "под ключ".
#
# Что делает:
# 1) Конфигурирует базовый маршрут (telegram -> owner).
# 2) Пытается автоматически зафиксировать chat_id через getUpdates.
# 3) Запускает строгую проверку маршрута.
# 4) (Опционально) отправляет тестовый алерт.
#
# Использование:
#   ./scripts/bootstrap_signal_alert_route.command
#   ./scripts/bootstrap_signal_alert_route.command --send-test
#   ./scripts/bootstrap_signal_alert_route.command --owner @username

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

SEND_TEST=0
OWNER_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --send-test)
      SEND_TEST=1
      shift
      ;;
    --owner)
      OWNER_OVERRIDE="${2:-}"
      if [[ -z "$OWNER_OVERRIDE" ]]; then
        echo "❌ Пустой owner после --owner"
        exit 2
      fi
      shift 2
      ;;
    *)
      echo "❌ Неизвестный аргумент: $1"
      echo "Использование: $0 [--send-test] [--owner @username]"
      exit 2
      ;;
  esac
done

echo "🚀 Signal Alert Route Bootstrap"
echo "--------------------------------------------------"

if [[ -n "$OWNER_OVERRIDE" ]]; then
  echo "1) configure_alert_route (owner override: $OWNER_OVERRIDE)"
  ./scripts/configure_alert_route.command telegram "$OWNER_OVERRIDE"
else
  echo "1) configure_alert_route"
  ./scripts/configure_alert_route.command
fi

echo
echo "2) resolve_telegram_alert_target"
if ./scripts/resolve_telegram_alert_target.command; then
  echo "✅ chat_id зафиксирован"
else
  echo "⚠️ chat_id пока не найден."
  echo "   Действие: открой @mytest_feb2026_bot и отправь /start, затем повтори bootstrap."
fi

echo
echo "3) strict route check"
if [[ "$SEND_TEST" -eq 1 ]]; then
  ./scripts/check_signal_alert_route.command --strict --send-test
else
  ./scripts/check_signal_alert_route.command --strict
fi

echo
echo "✅ Bootstrap завершен."
