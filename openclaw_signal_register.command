#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Register (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Регистрирует номер в signal-cli через captcha token.
# 2) Запрашивает SMS/voice verification code и завершает verify.
# 3) Подготавливает номер к запуску Signal daemon для OpenClaw.
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

echo
echo "1) Открой: https://signalcaptchas.org/registration/generate.html"
echo "2) Реши captcha"
echo "3) Скопируй ссылку Open Signal (начинается с signalcaptcha://...)"
echo
read "CAPTCHA_LINK?Вставь signalcaptcha-ссылку целиком и нажми Enter: "

if [[ -z "$CAPTCHA_LINK" ]]; then
  echo "❌ captcha ссылка пустая."
  exit 1
fi

CAPTCHA_TOKEN="${CAPTCHA_LINK#signalcaptcha://}"

echo
echo "⏳ Выполняю register..."
signal-cli -a "$SIGNAL_NUMBER" register --captcha "$CAPTCHA_TOKEN"

echo
echo "📩 Теперь введи verification code, который пришёл по SMS/voice."
read "VERIFY_CODE?Код подтверждения: "

if [[ -z "$VERIFY_CODE" ]]; then
  echo "❌ Код подтверждения пустой."
  exit 1
fi

echo "⏳ Выполняю verify..."
signal-cli -a "$SIGNAL_NUMBER" verify "$VERIFY_CODE"

echo
echo "✅ Signal номер зарегистрирован в signal-cli."
echo "Следующий шаг: запусти ./openclaw_signal_daemon.command"
echo
read -k "_ANY?Нажми любую клавишу для закрытия..."
echo
