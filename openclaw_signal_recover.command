#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Recovery (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Проверяет регистрацию номера в signal-cli.
# 2) Если номер зарегистрирован, запускает daemon и проверяет status/probe.
# 3) Если не зарегистрирован, предлагает два стабильных пути:
#    - register + verify
#    - secondary-device link
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source ./.env
  set +a
fi

if ! command -v signal-cli >/dev/null 2>&1; then
  echo "❌ signal-cli не найден. Установи: brew install signal-cli"
  exit 1
fi

SIGNAL_NUMBER="${OPENCLAW_SIGNAL_NUMBER:-}"
if [[ -z "$SIGNAL_NUMBER" ]]; then
  echo "⚠️ OPENCLAW_SIGNAL_NUMBER не задан в .env"
  read "SIGNAL_NUMBER?Введи номер Signal в формате +E164: "
fi

if [[ -z "$SIGNAL_NUMBER" ]]; then
  echo "❌ Номер не указан."
  exit 1
fi

echo "🔎 Проверяю регистрацию Signal для: $SIGNAL_NUMBER"
if signal-cli -a "$SIGNAL_NUMBER" listDevices >/dev/null 2>&1; then
  echo "✅ Номер уже зарегистрирован. Запускаю daemon..."
  ./openclaw_signal_daemon.command
  echo
  ./openclaw_signal_daemon_status.command || true
  exit 0
fi

echo
echo "⚠️ Номер пока не зарегистрирован в signal-cli."
echo "Выбери режим восстановления:"
echo "  [1] register + verify (captcha + SMS/voice)"
echo "  [2] link secondary device (через Signal на телефоне)"
echo "  [q] выход"
echo
read "RECOVERY_MODE?Твой выбор (1/2/q): "

case "${RECOVERY_MODE}" in
  1)
    ./openclaw_signal_register.command
    ;;
  2)
    ./openclaw_signal_link.command
    ;;
  q|Q)
    echo "Остановлено пользователем."
    exit 0
    ;;
  *)
    echo "❌ Неизвестный режим: ${RECOVERY_MODE}"
    exit 1
    ;;
esac

echo
echo "🔁 Пробую запустить daemon после recovery..."
if ./openclaw_signal_daemon.command; then
  echo
  ./openclaw_signal_daemon_status.command || true
  echo
  echo "✅ Recovery завершён."
else
  echo
  echo "⚠️ Recovery выполнен не до конца. Проверь шаги регистрации/линковки и запусти:"
  echo "   ./openclaw_signal_daemon_status.command"
fi

