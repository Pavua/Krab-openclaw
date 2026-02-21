#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Register (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Регистрирует номер в signal-cli через captcha token.
# 2) Запрашивает SMS/voice verification code и завершает verify.
# 3) Подготавливает номер к запуску Signal daemon для OpenClaw.
# Важно:
# - Если Signal вернул 429 Rate Limited, это серверный лимит Signal.
#   Скрипт покажет понятную причину и завершится без "молчаливого" падения.
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
echo "   Можно просто скопировать её в буфер: скрипт подхватит автоматически."
echo

# Приоритет источников captcha:
# 1) первый аргумент скрипта;
# 2) буфер обмена macOS (pbpaste), если там signalcaptcha://...;
# 3) интерактивный ввод в терминале.
CAPTCHA_LINK="${1:-}"
if [[ -z "$CAPTCHA_LINK" ]] && command -v pbpaste >/dev/null 2>&1; then
  CLIPBOARD_TEXT="$(pbpaste | tr -d '\r' | tr -d '\n')"
  if [[ "$CLIPBOARD_TEXT" == signalcaptcha://* ]]; then
    CAPTCHA_LINK="$CLIPBOARD_TEXT"
    echo "✅ Найдена signalcaptcha-ссылка в буфере обмена."
  fi
fi

# Поддержка передачи через файл:
# ./openclaw_signal_register.command @/tmp/signal_link.txt
if [[ "$CAPTCHA_LINK" == @* ]]; then
  CAPTCHA_FILE="${CAPTCHA_LINK#@}"
  if [[ -f "$CAPTCHA_FILE" ]]; then
    CAPTCHA_LINK="$(cat "$CAPTCHA_FILE")"
    echo "✅ Прочитана signalcaptcha-ссылка из файла: $CAPTCHA_FILE"
  fi
fi

if [[ -z "$CAPTCHA_LINK" ]]; then
  echo "⏳ Ожидаю ссылку в буфере обмена до 120 секунд..."
  for _ in {1..120}; do
    if command -v pbpaste >/dev/null 2>&1; then
      CLIPBOARD_TEXT="$(pbpaste | tr -d '[:space:]')"
      if [[ "$CLIPBOARD_TEXT" == signalcaptcha://* ]]; then
        CAPTCHA_LINK="$CLIPBOARD_TEXT"
        echo "✅ Ссылка автоматически подхвачена из буфера."
        break
      fi
    fi
    sleep 1
  done
fi

if [[ -z "$CAPTCHA_LINK" ]]; then
  # Используем read -r, чтобы не ломать длинные строки и спецсимволы.
  read -r "CAPTCHA_LINK?Вставь signalcaptcha-ссылку целиком и нажми Enter: "
fi

if [[ -z "$CAPTCHA_LINK" ]]; then
  echo "❌ captcha ссылка пустая."
  exit 1
fi

# Нормализуем ссылку: убираем пробелы/переносы, если чат/терминал их добавил.
CAPTCHA_LINK="$(printf "%s" "$CAPTCHA_LINK" | tr -d '[:space:]')"

# Поддерживаем оба формата:
# 1) полный URI: signalcaptcha://...
# 2) только токен (если пользователь вставил уже без префикса)
if [[ "$CAPTCHA_LINK" == signalcaptcha://* ]]; then
  CAPTCHA_TOKEN="${CAPTCHA_LINK#signalcaptcha://}"
else
  CAPTCHA_TOKEN="$CAPTCHA_LINK"
fi

echo
echo "⏳ Выполняю register..."
REGISTER_LOG="$(mktemp -t signal-register.XXXXXX.log)"
set +e
signal-cli -a "$SIGNAL_NUMBER" register --captcha "$CAPTCHA_TOKEN" 2>&1 | tee "$REGISTER_LOG"
REGISTER_EXIT=$?
set -e

if [[ $REGISTER_EXIT -ne 0 ]]; then
  if rg -q "429|Rate Limited" "$REGISTER_LOG"; then
    echo
    echo "⛔ Signal вернул 429 Rate Limited."
    echo "   Это внешний лимит со стороны Signal (не ошибка твоего конфига OpenClaw)."
    echo "   Подожди 30-60 минут и повтори запуск скрипта с новой captcha."
    echo "   Если лимит держится дольше, подожди до 24 часов."
    rm -f "$REGISTER_LOG"
    exit 2
  fi

  if rg -q "proof required|challenge" "$REGISTER_LOG"; then
    echo
    echo "⚠️ Signal запросил challenge (proof required)."
    echo "   Для этого нужен challenge token из ошибки и отдельная команда:"
    echo "   signal-cli submitRateLimitChallenge --challenge <TOKEN> --captcha <CAPTCHA_TOKEN>"
  fi

  echo
  echo "❌ Регистрация не завершилась успешно. См. вывод выше."
  rm -f "$REGISTER_LOG"
  exit $REGISTER_EXIT
fi

rm -f "$REGISTER_LOG"

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
