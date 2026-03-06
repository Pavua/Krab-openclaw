#!/bin/bash
# =============================================================================
# Проверка готовности маршрута Signal-алертов в Telegram.
# Зачем: быстро убедиться, что аварийные уведомления действительно дойдут.
# Связь: используется в runbook и pre_release_smoke.py (опциональная проверка).
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

SEND_TEST=0
STRICT_MODE=0
for arg in "$@"; do
  case "$arg" in
    --send-test)
      SEND_TEST=1
      ;;
    --strict)
      STRICT_MODE=1
      ;;
    *)
      echo "❌ Неизвестный аргумент: $arg"
      echo "Использование: $0 [--strict] [--send-test]"
      exit 2
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "❌ .env не найден"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

fail_count=0
warn_count=0

say_ok() { echo "✅ $1"; }
say_warn() { echo "⚠️ $1"; warn_count=$((warn_count + 1)); }
say_fail() { echo "❌ $1"; fail_count=$((fail_count + 1)); }

echo "🔎 Signal Alert Route Check"
echo "--------------------------------------------------"

if [[ -n "${OPENCLAW_TELEGRAM_BOT_TOKEN:-}" ]]; then
  say_ok "OPENCLAW_TELEGRAM_BOT_TOKEN задан"
else
  say_fail "OPENCLAW_TELEGRAM_BOT_TOKEN не задан"
fi

if [[ -n "${OPENCLAW_ALERT_CHANNEL:-}" ]]; then
  say_ok "OPENCLAW_ALERT_CHANNEL=${OPENCLAW_ALERT_CHANNEL}"
else
  say_warn "OPENCLAW_ALERT_CHANNEL пуст (обычно telegram)"
fi

if [[ -n "${OPENCLAW_ALERT_TARGET:-}" ]]; then
  say_ok "OPENCLAW_ALERT_TARGET=${OPENCLAW_ALERT_TARGET}"
else
  say_warn "OPENCLAW_ALERT_TARGET пуст"
fi

if [[ -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
  say_ok "OPENCLAW_TELEGRAM_CHAT_ID=${OPENCLAW_TELEGRAM_CHAT_ID}"
else
  say_warn "OPENCLAW_TELEGRAM_CHAT_ID не задан"
fi

echo
echo "📡 Проверка статуса Telegram канала..."
status_output="$(openclaw channels status --probe 2>&1 || true)"
if echo "$status_output" | rg -q "Telegram.*works|telegram.*works|default.*works"; then
  say_ok "openclaw Telegram канал в статусе works"
else
  say_fail "openclaw Telegram канал не в works"
  echo "$status_output" | rg -n "Telegram|telegram|Warnings|warning|error|failed" || true
fi

echo
echo "🤖 Проверка getUpdates у Telegram бота..."
updates_info="$({
python3 - <<'PY'
import json
import os
import time
import urllib.request

token = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    print("ok=0")
    print("updates=0")
    print("private_chat_id=")
    print("error=no_token")
    raise SystemExit(0)

url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
data = None
last_error = ""
for attempt in range(1, 4):
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        break
    except Exception as exc:
        last_error = str(exc)
        if attempt < 3:
            time.sleep(1.5)

if not isinstance(data, dict):
    print("ok=0")
    print("updates=0")
    print("private_chat_id=")
    print(f"error={last_error}")
    raise SystemExit(0)

ok = bool(data.get("ok"))
updates = data.get("result", [])
private_chat_id = ""
for upd in reversed(updates):
    for key in ("message", "edited_message", "my_chat_member", "channel_post"):
        msg = upd.get(key)
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            continue
        if str(chat.get("type", "")).lower() == "private":
            private_chat_id = str(chat.get("id", ""))
            break
    if private_chat_id:
        break

print(f"ok={1 if ok else 0}")
print(f"updates={len(updates)}")
print(f"private_chat_id={private_chat_id}")
print("error=")
PY
} || true)"

updates_ok="$(echo "$updates_info" | awk -F= '/^ok=/{print $2}')"
updates_count="$(echo "$updates_info" | awk -F= '/^updates=/{print $2}')"
private_chat_id="$(echo "$updates_info" | awk -F= '/^private_chat_id=/{print $2}')"
updates_error="$(echo "$updates_info" | awk -F= '/^error=/{print substr($0,7)}')"

if [[ "$updates_ok" == "1" ]]; then
  say_ok "Bot API getUpdates доступен"
else
  say_fail "Bot API getUpdates недоступен"
  if [[ -n "${updates_error:-}" ]]; then
    echo "   detail: ${updates_error}"
  fi
fi

if [[ "${updates_count:-0}" -gt 0 ]]; then
  say_ok "У бота есть updates: ${updates_count}"
else
  say_warn "У бота нет updates (возможно, /start ещё не отправляли)"
fi

if [[ -n "${private_chat_id:-}" ]]; then
  say_ok "Найден private chat_id в updates: ${private_chat_id}"
else
  say_warn "Private chat_id не найден в updates"
fi

if [[ -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" && -n "${private_chat_id:-}" ]]; then
  if [[ "${OPENCLAW_TELEGRAM_CHAT_ID}" == "${private_chat_id}" ]]; then
    say_ok "OPENCLAW_TELEGRAM_CHAT_ID совпадает с private chat_id"
  else
    say_warn "OPENCLAW_TELEGRAM_CHAT_ID отличается от private chat_id"
  fi
fi

if [[ "${OPENCLAW_ALERT_CHANNEL:-telegram}" == "telegram" && -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
  if [[ "${OPENCLAW_ALERT_TARGET:-}" == "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
    say_ok "OPENCLAW_ALERT_TARGET синхронизирован с chat_id"
  else
    say_warn "OPENCLAW_ALERT_TARGET не синхронизирован с OPENCLAW_TELEGRAM_CHAT_ID"
  fi
fi

echo "--------------------------------------------------"
if [[ "$SEND_TEST" -eq 1 ]]; then
  echo "🧪 Запуск test alert..."
  if [[ -x "./scripts/signal_alert_test.command" ]]; then
    if ./scripts/signal_alert_test.command; then
      say_ok "signal_alert_test прошел"
    else
      say_fail "signal_alert_test не прошел"
    fi
  else
    say_warn "signal_alert_test.command отсутствует, тест отправки пропущен"
  fi
fi

if [[ "$STRICT_MODE" -eq 1 && "$warn_count" -gt 0 ]]; then
  echo "Итог: FAIL (strict mode, предупреждений: ${warn_count})"
  exit 1
fi

if [[ "$fail_count" -gt 0 ]]; then
  echo "Итог: FAIL (${fail_count} критичных, ${warn_count} предупреждений)"
  exit 1
fi

echo "Итог: OK (${warn_count} предупреждений)"
exit 0
